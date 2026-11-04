"""Python types as OpenAPI schemas, request type checking, and JSON / query-string I/O.

Requests are checked against their *types* only (``c.req.valid(...)``); ``Annotated`` keywords
(``minLength``, ``maximum``, ``pattern``, ...) are documentation and are never checked. Supported:
``str``, ``int``, ``float``, ``bool``, ``None``, ``datetime``, ``date``, ``UUID``, ``list[T]``,
``tuple[T, ...]``, ``set[T]``, ``dict[str, T]``, unions (``T | None``), ``Literal[...]``, ``Enum``
subclasses, ``TypedDict`` (``Required`` / ``NotRequired``), dataclasses and ``Any``.
``Annotated[T, {...}]`` adds JSON Schema keywords to the document. Dataclasses, TypedDicts and
Enums are named types: they go to ``components.schemas`` and are referenced with ``$ref``.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import functools
import json
import math
import re
import types
import typing
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Literal, NamedTuple, NotRequired, Required, Union, get_args, get_origin
from urllib.parse import parse_qsl

try:  # Rust (the maturin-built extension): the same results as json / parse_qsl, faster
    from takibiapi import _core as _core  # re-exported: the router and the tests use it
except ImportError:  # pragma: no cover - a source checkout without the extension built
    _core = None

_NONE = type(None)
_FORMATS: dict[type, str] = {_dt.datetime: "date-time", _dt.date: "date", uuid.UUID: "uuid"}
_PRIMITIVES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}
_ARRAYS = (list, tuple, set, frozenset)
# Text accepted for int / float parts (ASCII only, as JSON numbers; no "1_000", " 1", "nan").
_INT = re.compile(r"[+-]?[0-9]+")
_FLOAT = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?")


class ValidationError(Exception):
    """A value does not match its type. ``issues``: ``[{"path", "code", "message"}]``."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{'.'.join(map(str, i['path'])) or '(root)'}: {i['message']}" for i in issues))
        self.issues = issues


class Field(NamedTuple):
    name: str
    type: Any
    required: bool
    default: Any = dataclasses.MISSING  # a value, a default_factory (factory=True), or MISSING
    factory: bool = False


def is_object_type(tp: Any) -> bool:
    return isinstance(tp, type) and (dataclasses.is_dataclass(tp) or typing.is_typeddict(tp))


@functools.cache
def fields(tp: type) -> tuple[Field, ...]:
    """The fields of a dataclass or TypedDict, with resolved annotations."""
    hints = typing.get_type_hints(tp, include_extras=True)
    if dataclasses.is_dataclass(tp):
        out = []
        for f in dataclasses.fields(tp):
            default, factory = f.default, False
            if default is dataclasses.MISSING and f.default_factory is not dataclasses.MISSING:
                default, factory = f.default_factory, True
            out.append(Field(f.name, hints[f.name], default is dataclasses.MISSING, default, factory))
        return tuple(out)
    total = getattr(tp, "__total__", True)
    out = []
    for name, hint in hints.items():
        required = total
        origin = get_origin(hint)
        if origin is Required or origin is NotRequired:
            required = origin is Required
            hint = get_args(hint)[0]
        out.append(Field(name, hint, required))
    return tuple(out)


def unwrap(tp: Any) -> tuple[Any, dict[str, Any]]:
    """``Annotated[T, {...}]`` -> ``(T, {...})``; other types -> ``(tp, {})``."""
    meta: dict[str, Any] = {}
    while get_origin(tp) is typing.Annotated:
        tp, *extras = get_args(tp)
        for extra in extras:
            if isinstance(extra, Mapping):
                meta.update(extra)
    if get_origin(tp) is Required or get_origin(tp) is NotRequired:
        return unwrap(get_args(tp)[0])
    return tp, meta


def _is_union(origin: Any) -> bool:
    return origin is Union or origin is types.UnionType


def _is_array(tp: Any) -> bool:
    return tp in _ARRAYS or get_origin(tp) in _ARRAYS


def is_sequence(tp: Any) -> bool:
    """``list[T]``, ``tuple[T, ...]``, a set, or one of them ``| None`` (repeated query keys)."""
    tp = unwrap(tp)[0]
    members = get_args(tp) if _is_union(get_origin(tp)) else (tp,)
    return any(_is_array(unwrap(m)[0]) for m in members)


# -- JSON Schema ------------------------------------------------------------------------------ #
class SchemaBuilder:
    """Converts types to JSON Schema, collecting named types into :attr:`components`."""

    def __init__(self, openapi31: bool = False) -> None:
        self.openapi31 = openapi31
        self.components: dict[str, dict[str, Any]] = {}
        self._named: dict[str, type] = {}

    def of(self, tp: Any) -> dict[str, Any]:
        tp, meta = unwrap(tp)
        schema = self._base(tp)
        if meta:
            if "$ref" in schema and not self.openapi31:  # 3.0 ignores siblings of $ref
                schema = {"allOf": [schema]}
            schema = {**schema, **meta}
        return schema

    def _base(self, tp: Any) -> dict[str, Any]:
        if tp is Any or tp is object:
            return {}
        if tp is None or tp is _NONE:
            return {"type": "null"} if self.openapi31 else {"nullable": True, "enum": [None]}
        if tp in _PRIMITIVES:
            return {"type": _PRIMITIVES[tp]}
        if tp in _FORMATS:
            return {"type": "string", "format": _FORMATS[tp]}
        origin = get_origin(tp)
        if _is_union(origin):
            return self._union(get_args(tp))
        if origin is Literal:
            values = get_args(tp)
            if None in values:
                rest = tuple(v for v in values if v is not None)
                return self._nullable(self._literal(rest)) if rest else self._base(None)
            return self._literal(values)
        if _is_array(tp):
            args = get_args(tp)
            if origin is tuple and args and (len(args) != 2 or args[1] is not Ellipsis):
                raise TypeError(f"only homogeneous tuples (tuple[T, ...]) are supported: {tp!r}")
            schema: dict[str, Any] = {"type": "array", "items": self.of(args[0]) if args else {}}
            if tp in (set, frozenset) or origin in (set, frozenset):
                schema["uniqueItems"] = True
            return schema
        if tp is dict or origin is dict:
            args = get_args(tp)
            if args and unwrap(args[0])[0] is not str:
                raise TypeError(f"JSON object keys are strings: {tp!r}")
            return {"type": "object", "additionalProperties": self.of(args[1]) if args else {}}
        if isinstance(tp, type) and issubclass(tp, enum.Enum):
            return self._ref(tp, lambda: self._literal(tuple(m.value for m in tp)))
        if is_object_type(tp):
            return self._ref(tp, lambda: self._object(tp))
        raise TypeError(f"no JSON Schema for {tp!r}")

    def _ref(self, tp: type, build: Any) -> dict[str, Any]:
        name = tp.__name__
        known = self._named.get(name)
        if known is None:
            self._named[name] = tp
            self.components[name] = {}  # reserve the name first: recursive types refer to it
            self.components[name] = build()
        elif known is not tp:
            raise TypeError(f"two schemas named {name!r}: {known!r} and {tp!r}")
        return {"$ref": f"#/components/schemas/{name}"}

    def _object(self, tp: type) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for f in fields(tp):
            prop = self.of(f.type)
            if f.default is not dataclasses.MISSING and not f.factory and _jsonable(f.default):
                prop = {**prop, "default": _plain(f.default)}
            properties[f.name] = prop
            if f.required:
                required.append(f.name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def _union(self, args: tuple[Any, ...]) -> dict[str, Any]:
        members = [a for a in args if a is not _NONE and a is not None]
        inner = self.of(members[0]) if len(members) == 1 else {"anyOf": [self.of(m) for m in members]}
        return self._nullable(inner) if len(members) < len(args) else inner

    def _nullable(self, schema: dict[str, Any]) -> dict[str, Any]:
        if self.openapi31:
            if isinstance(schema.get("type"), str):
                return {**schema, "type": [schema["type"], "null"]}
            return {"anyOf": [schema, {"type": "null"}]}
        if "$ref" in schema:
            return {"allOf": [schema], "nullable": True}
        return {**schema, "nullable": True}

    @staticmethod
    def _literal(values: tuple[Any, ...]) -> dict[str, Any]:
        schema: dict[str, Any] = {"enum": list(values)}
        kinds = {type(v) for v in values}
        if len(kinds) == 1 and (kind := kinds.pop()) in _PRIMITIVES:
            schema = {"type": _PRIMITIVES[kind], **schema}
        return schema


def _jsonable(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, enum.Enum))


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, enum.Enum) else value


# -- type checking --------------------------------------------------------------------------- #
def decode(tp: Any, value: Any, *, coerce: bool = False) -> Any:
    """Check ``value`` against the type ``tp`` and build the typed value (dataclass instances,
    ``datetime``, Enum members). ``coerce``: ``value`` came as text (path, query, header, cookie),
    so ``"1"`` is accepted as an ``int`` and ``"true"`` as a ``bool``. Raises
    :class:`ValidationError`. Only types are checked, never ``Annotated`` keywords.

    This is the pure-Python reference; :class:`Codec` uses the Rust engine when it is built."""
    return _Checker(coerce=coerce).run(tp, value)


class _Checker:
    def __init__(self, *, coerce: bool) -> None:
        self.coerce = coerce
        self.issues: list[dict[str, Any]] = []

    def run(self, tp: Any, value: Any) -> Any:
        out = self.check(tp, value, [])
        if self.issues:
            raise ValidationError(self.issues)
        return out

    def fail(self, path: list[Any], code: str, message: str) -> None:
        self.issues.append({"path": list(path), "code": code, "message": message})

    def check(self, tp: Any, value: Any, path: list[Any]) -> Any:
        tp = unwrap(tp)[0]
        if tp is Any or tp is object:
            return value
        if tp is None or tp is _NONE:
            if value is None:
                return None
            return self._type_error(path, "null", value)
        if tp is bool:
            if self.coerce and isinstance(value, str) and value in ("true", "false"):
                return value == "true"
            return value if type(value) is bool else self._type_error(path, "boolean", value)
        if tp is int:
            if self.coerce and isinstance(value, str) and _INT.fullmatch(value):
                return int(value)
            if isinstance(value, int) and type(value) is not bool:
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            return self._type_error(path, "integer", value)
        if tp is float:
            if self.coerce and isinstance(value, str) and _FLOAT.fullmatch(value):
                value = float(value)
            if isinstance(value, (int, float)) and type(value) is not bool and math.isfinite(value):
                return float(value)
            return self._type_error(path, "number", value)
        if tp is str:
            return value if isinstance(value, str) else self._type_error(path, "string", value)
        if tp in _FORMATS:
            return self._format(tp, value, path)
        origin = get_origin(tp)
        if _is_union(origin):
            return self._union(get_args(tp), value, path)
        if origin is Literal:
            return self._literal(get_args(tp), value, path)
        if _is_array(tp):
            if not isinstance(value, _ARRAYS):
                return self._type_error(path, "array", value)
            args = get_args(tp)
            item = args[0] if args else Any
            before = len(self.issues)
            items = [self.check(item, v, [*path, i]) for i, v in enumerate(value)]
            if (origin or tp) is not list and len(self.issues) == before:
                return (origin or tp)(items)
            return items
        if tp is dict or origin is dict:
            if not isinstance(value, Mapping):
                return self._type_error(path, "object", value)
            args = get_args(tp)
            item = args[1] if args else Any
            return {str(k): self.check(item, v, [*path, str(k)]) for k, v in value.items()}
        if isinstance(tp, type) and issubclass(tp, enum.Enum):
            return self._enum(tp, value, path)
        if is_object_type(tp):
            return self._object(tp, value, path)
        raise TypeError(f"cannot check values of type {tp!r}")

    def _type_error(self, path: list[Any], expected: str, value: Any) -> Any:
        received = "null" if value is None else type(value).__name__
        self.fail(path, "invalid_type", f"Expected {expected}, received {received}")
        return value

    def _format(self, tp: type, value: Any, path: list[Any]) -> Any:
        if isinstance(value, str):
            try:
                if tp is uuid.UUID:
                    return uuid.UUID(value)
                if tp is _dt.date and ("T" in value or " " in value):
                    raise ValueError
                return tp.fromisoformat(value)  # type: ignore[attr-defined]
            except ValueError:
                pass
        self.fail(path, "invalid_format", f"Expected {_FORMATS[tp]}")
        return value

    def _union(self, args: tuple[Any, ...], value: Any, path: list[Any]) -> Any:
        if value is None and (_NONE in args or None in args):
            return None
        for member in args:
            attempt = _Checker(coerce=self.coerce)
            out = attempt.check(member, value, path)
            if not attempt.issues:
                return out
        if len(args) == 2 and _NONE in args:  # T | None: report T's own issues
            return self.check(next(a for a in args if a is not _NONE), value, path)
        self.fail(path, "invalid_union", "Value does not match any of the allowed types")
        return value

    def _literal(self, allowed: tuple[Any, ...], value: Any, path: list[Any]) -> Any:
        for option in allowed:
            if type(value) is type(option) and value == option:
                return value
            if self.coerce and isinstance(value, str) and not isinstance(option, str) and option is not None:
                attempt = _Checker(coerce=True)
                converted = attempt.check(type(option), value, path)
                if not attempt.issues and converted == option:
                    return option
        self.fail(path, "invalid_value", f"Expected one of {list(allowed)!r}")
        return value

    def _enum(self, tp: type[enum.Enum], value: Any, path: list[Any]) -> Any:
        if isinstance(value, tp):
            return value
        values = tuple(m.value for m in tp)
        checker = _Checker(coerce=self.coerce)
        raw = checker._literal(values, value, path)
        if checker.issues:
            self.fail(path, "invalid_value", f"Expected one of {list(values)!r}")
            return value
        return tp(raw)

    def _object(self, tp: type, value: Any, path: list[Any]) -> Any:
        if not isinstance(value, Mapping):
            return self._type_error(path, "object", value)
        before = len(self.issues)
        out: dict[str, Any] = {}
        for f in fields(tp):
            if f.name not in value:
                if f.required:
                    self.fail([*path, f.name], "missing", "Required")
                continue
            out[f.name] = self.check(f.type, value[f.name], [*path, f.name])
        if dataclasses.is_dataclass(tp) and len(self.issues) == before:
            return tp(**out)
        return out


# -- the Rust engine ---------------------------------------------------------------------------- #
# ``takibiapi._core`` (Rust) checks from a *plan*: the type walked once, here, into
# plain nodes. The checker above is the fallback when the extension is not built, and the
# reference the Rust engine is tested against.
def plan(tp: Any) -> dict[str, Any]:
    """``{"root": node, "defs": [object definitions]}``; named object types are ``{"k": "ref"}``
    nodes into ``defs``, so recursive types are fine. ``Annotated`` keywords are left out."""
    defs: list[Any] = []
    index: dict[Any, int] = {}

    def node(tp: Any) -> dict[str, Any]:
        tp = unwrap(tp)[0]
        if tp is Any or tp is object:
            return {"k": "any"}
        if tp is None or tp is _NONE:
            return {"k": "none"}
        if tp in (bool, int, float, str):
            return {"k": tp.__name__}
        if tp is _dt.datetime:
            return {"k": "datetime"}
        if tp is _dt.date:
            return {"k": "date"}
        if tp is uuid.UUID:
            return {"k": "uuid"}
        origin = get_origin(tp)
        if _is_union(origin):
            args = get_args(tp)
            members = [node(a) for a in args if a is not _NONE and a is not None]
            return {"k": "union", "members": members, "nullable": len(members) < len(args)}
        if origin is Literal:
            return {"k": "literal", "values": list(get_args(tp))}
        if _is_array(tp):
            args = get_args(tp)
            return {"k": "list", "items": node(args[0] if args else Any), "container": (origin or tp).__name__}
        if tp is dict or origin is dict:
            args = get_args(tp)
            return {"k": "dict", "values": node(args[1] if args else Any)}
        if isinstance(tp, type) and issubclass(tp, enum.Enum):
            members = list(tp)
            return {"k": "enum", "cls": tp, "values": [m.value for m in members], "members": members}
        if is_object_type(tp):
            if tp not in index:
                index[tp] = len(defs)
                defs.append(None)
                defs[index[tp]] = {
                    "name": tp.__name__,
                    "cls": tp if dataclasses.is_dataclass(tp) else None,
                    "direct": _direct(tp),
                    "fields": [
                        {
                            "name": f.name,
                            "node": node(f.type),
                            "required": f.required,
                            "default": f.default,
                            "has_default": f.default is not dataclasses.MISSING,
                            "factory": f.factory,
                        }
                        for f in fields(tp)
                    ],
                }
            return {"k": "ref", "i": index[tp]}
        raise TypeError(f"cannot check values of type {tp!r}")

    root = node(tp)
    return {"root": root, "defs": defs}


def _direct(tp: Any) -> bool:
    """Whether Rust may build ``tp`` as ``object.__new__(tp)`` plus its ``__dict__``, skipping
    ``__init__``: only for a plain dataclass whose generated ``__init__`` would do just that."""
    if not dataclasses.is_dataclass(tp) or type(tp) is not type:
        return False
    init = tp.__dict__.get("__init__")
    code = getattr(init, "__code__", None)
    return (
        code is not None
        and code.co_filename == "<string>"  # generated by @dataclass, not written by hand
        and not hasattr(tp, "__post_init__")
        and not hasattr(tp, "__slots__")
        and tp.__new__ is object.__new__
        and all(f.init for f in dataclasses.fields(tp))
        and not any(isinstance(f.type, dataclasses.InitVar) for f in tp.__dataclass_fields__.values())
    )


class Codec:
    """A type compiled once (at route registration) to check request parts against it."""

    __slots__ = ("_rust", "type")

    def __init__(self, tp: Any) -> None:
        self.type = tp
        compiled = plan(tp)  # unsupported types fail here, with either engine
        self._rust = _core.Validator(compiled) if _core is not None else None

    def decode(self, value: Any, *, coerce: bool = False) -> Any:
        """:func:`decode` (``value`` from Python: path / query / header / cookie parts)."""
        if self._rust is not None:
            ok, out = self._rust.decode(value, coerce)
            if ok:
                return out
            raise ValidationError(out)
        return decode(self.type, value, coerce=coerce)

    def decode_json(self, data: bytes) -> Any:
        """Parse and check a JSON body in one pass. Raises ``ValueError`` for malformed JSON and
        :class:`ValidationError` for a body of the wrong type."""
        if self._rust is not None:
            result = self._rust.decode_json(data)
            if result is not None:  # None: input Rust leaves to Python (e.g. lone surrogates)
                ok, out = result
                if ok:
                    return out
                raise ValidationError(out)
        return self.decode(json.loads(data))


# -- JSON and query strings ---------------------------------------------------------------------- #
def to_json(value: Any) -> Any:
    """JSON values for what ``json`` cannot write itself: dataclasses, Enums, dates, UUIDs, sets."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json(v) for v in value]
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


# One encoder instance: ``json.dumps`` re-reads its keyword arguments on every call.
_py_encode = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), default=to_json).encode


def dumps(value: Any, default: Callable[[Any], Any] | None = None) -> bytes:
    """Compact UTF-8 JSON; dataclasses, Enums, ``datetime`` and ``UUID`` are converted, and
    ``default`` (as in ``json.dumps``) converts the objects nothing else does. Rust writes what it
    can reproduce exactly; anything else (e.g. non-``str`` keys) goes through ``json``."""
    if _core is not None:
        out = _core.dumps(value, default)
        if out is not None:
            return out
    if default is None:
        return _utf8(_py_encode(value), value, to_json)

    def chained(o: Any) -> Any:
        out = to_json(o)
        return default(o) if out is o else out

    return _utf8(
        json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), default=chained).encode(value), value, chained
    )


def _utf8(text: str, value: Any, default: Callable[[Any], Any]) -> bytes:
    """``text`` as UTF-8; text with a lone surrogate (which UTF-8 cannot carry) is written again
    with ASCII escapes (``"\\ud800"``), still valid JSON for the same value."""
    try:
        return text.encode()
    except UnicodeEncodeError:
        return json.JSONEncoder(separators=(",", ":"), default=default).encode(value).encode()


def loads(data: bytes) -> Any:
    """``json.loads``; raises ``ValueError`` for malformed JSON."""
    if _core is not None:
        out = _core.loads(data)
        if out is not None:
            return out[0]
    return json.loads(data)


def parse_query(qs: bytes) -> dict[str, list[str]]:
    """``{key: [values]}``, as ``urllib.parse.parse_qsl(..., keep_blank_values=True)`` gives them."""
    if _core is not None:
        out = _core.parse_query(qs)
        if out is not None:
            return out
    values: dict[str, list[str]] = {}
    for key, value in parse_qsl(qs.decode("latin-1"), keep_blank_values=True):
        values.setdefault(key, []).append(value)
    return values


__all__ = [
    "Codec",
    "Field",
    "SchemaBuilder",
    "ValidationError",
    "decode",
    "dumps",
    "fields",
    "is_object_type",
    "is_sequence",
    "loads",
    "parse_query",
    "plan",
    "to_json",
    "unwrap",
]
