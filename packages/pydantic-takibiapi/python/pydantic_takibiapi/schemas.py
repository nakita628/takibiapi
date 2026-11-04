"""``PydanticSchemas``: pydantic models as takibi's schemas (see ``takibiapi.schemas``).

Requests are checked with pydantic, constraints included (``Field(ge=1, min_length=1)``):
parameters from the path, query, headers and cookies are read from their text, JSON bodies
strictly (``"1"`` is not an ``int``) and without undeclared keys. Responses are checked against
the model the route declares for the status before they are written. Every model must be strict
(``Model``, or ``strict=True`` and ``extra="forbid"``).
"""

from __future__ import annotations

import enum
import functools
import types
import typing
from collections.abc import Iterator
from typing import Any, Literal, Union, get_args, get_origin

import pydantic
from pydantic import BaseModel, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaMode

from takibiapi import ValidationError

from .model import ValidationProblem

REF_TEMPLATE = "#/components/schemas/{model}"


def _issues(exc: pydantic.ValidationError) -> list[dict[str, Any]]:
    return [{"path": list(e["loc"]), "code": e["type"], "message": e["msg"]} for e in exc.errors(include_url=False)]


def _strict_error(tp: Any) -> str | None:
    """Why ``tp`` is not strict enough, or None: every model must be strict and forbid extra keys,
    and plain dataclasses / TypedDicts (whose settings cannot say so) are not allowed."""
    seen: set[int] = set()

    def walk(tp: Any) -> Iterator[str]:
        origin = get_origin(tp)
        if origin is typing.Annotated:
            yield from walk(get_args(tp)[0])
            return
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            if id(tp) in seen:
                return
            seen.add(id(tp))
            config = tp.model_config
            if config.get("strict") is not True or config.get("extra") != "forbid":
                yield f"{tp.__name__} is not strict (subclass pydantic_takibiapi.Model)"
            for field in tp.model_fields.values():
                yield from walk(field.annotation)
            return
        if typing.is_typeddict(tp) or (isinstance(tp, type) and hasattr(tp, "__dataclass_fields__")):
            yield f"{tp.__name__} is a {'TypedDict' if typing.is_typeddict(tp) else 'dataclass'} (use a Model)"
            return
        for arg in get_args(tp):
            if not isinstance(arg, (str, int, float, bool, enum.Enum)) and arg is not Ellipsis and arg is not None:
                yield from walk(arg)

    return next(walk(tp), None)


def _strict_adapter(tp: Any) -> TypeAdapter[Any]:
    error = _strict_error(tp)
    if error is not None:
        raise TypeError(error)
    return TypeAdapter(tp)


def _repeats(annotation: Any) -> bool:
    """Whether a query field collects every value of its key (``list[str]``, ``list[int] | None``)."""
    args = get_args(annotation) if get_origin(annotation) in (Union, types.UnionType) else (annotation,)
    return any(a in (list, tuple, set, frozenset) or get_origin(a) in (list, tuple, set, frozenset) for a in args)


class _Checker:
    __slots__ = ("_adapter", "names")

    def __init__(self, tp: Any, target: str) -> None:
        self._adapter = _strict_adapter(tp)
        if target == "json":
            self.names: list[tuple[str, bool]] = []
            return
        if not (isinstance(tp, type) and issubclass(tp, BaseModel)):
            raise TypeError(f"request {target} parts must be a Model, got {tp!r}")
        self.names = [(f.alias or name, _repeats(f.annotation)) for name, f in tp.model_fields.items()]

    def decode(self, value: Any, *, coerce: bool) -> Any:
        try:  # parameters arrive as text: convert ("7" -> 7) even though the model is strict
            return self._adapter.validate_python(value, strict=False if coerce else None)
        except pydantic.ValidationError as exc:
            raise ValidationError(_issues(exc)) from None

    def decode_json(self, data: bytes) -> Any:
        try:
            return self._adapter.validate_json(data)
        except pydantic.ValidationError as exc:
            errors = exc.errors(include_url=False)
            if errors and errors[0]["type"] == "json_invalid":
                raise ValueError(errors[0]["msg"]) from None
            raise ValidationError(_issues(exc)) from None


class _Encoder:
    __slots__ = ("_adapter",)

    def __init__(self, tp: Any) -> None:
        self._adapter = _strict_adapter(tp)

    def encode_json(self, value: Any) -> bytes:
        try:
            checked = self._adapter.validate_python(value)
        except pydantic.ValidationError as exc:
            raise ValidationError(_issues(exc)) from None
        return self._adapter.dump_json(checked)


class _DocBuilder:
    """Collects every schema the document needs and generates them in one pydantic pass, so named
    models are written once to ``components.schemas`` (with ``-Input`` / ``-Output`` variants when
    a model reads and writes differently). ``of`` returns a placeholder dict, filled in place when
    ``components`` is read (``build_document`` reads it last)."""

    def __init__(self, openapi31: bool) -> None:
        self.openapi31 = openapi31
        self._pending: list[tuple[dict[str, Any], JsonSchemaMode, TypeAdapter[Any]]] = []
        self._components: dict[str, Any] | None = None

    def of(self, tp: Any, io: Literal["input", "output"] = "input") -> dict[str, Any]:
        placeholder: dict[str, Any] = {}
        mode: JsonSchemaMode = "validation" if io == "input" else "serialization"
        self._pending.append((placeholder, mode, _adapter_for_doc(tp)))
        return placeholder

    def parameters(self, tp: Any, location: str) -> list[dict[str, Any]]:
        if not (isinstance(tp, type) and issubclass(tp, BaseModel)):
            raise TypeError(f"{location} parameters must be a Model, got {tp!r}")
        out: list[dict[str, Any]] = []
        for name, field in tp.model_fields.items():
            param: dict[str, Any] = {
                "name": field.alias or name,
                "in": location,
                "required": location == "path" or field.is_required(),
                "schema": self.of(typing.Annotated[field.annotation, _without_alias(field)]),
            }
            if field.description:
                param["description"] = field.description
            out.append(param)
        return out

    @property
    def components(self) -> dict[str, Any]:
        if self._components is None:
            inputs: list[tuple[int, JsonSchemaMode, TypeAdapter[Any]]] = [
                (i, mode, adapter) for i, (_, mode, adapter) in enumerate(self._pending)
            ]
            schemas, definitions = TypeAdapter.json_schemas(inputs, ref_template=REF_TEMPLATE)
            for i, (placeholder, mode, _) in enumerate(self._pending):
                placeholder.update(self._convert(_untitled(schemas[(i, mode)])))
            defs = definitions.get("$defs", {})
            self._components = {name: self._convert(schema) for name, schema in defs.items()}
        return self._components

    def _convert(self, schema: Any) -> Any:
        return schema if self.openapi31 else to_openapi30(schema)


@functools.cache
def _adapter_for_doc(tp: Any) -> TypeAdapter[Any]:
    return TypeAdapter(tp)


def _without_alias(field: FieldInfo) -> FieldInfo:
    """The field's constraints and description, for a parameter's own schema."""
    return FieldInfo.merge_field_infos(field, alias=None, validation_alias=None, serialization_alias=None, title=None)


def _untitled(schema: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in schema.items() if k != "title"}


def to_openapi30(schema: Any) -> Any:
    """pydantic's JSON Schema (2020-12, as OpenAPI 3.1 uses) written for OpenAPI 3.0: ``null`` in
    unions becomes ``nullable``, ``const`` an ``enum``, ``examples`` an ``example``, numeric
    ``exclusiveMinimum`` / ``exclusiveMaximum`` the boolean form, and ``$ref`` siblings ``allOf``."""
    if isinstance(schema, list):
        return [to_openapi30(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: to_openapi30(v) if isinstance(v, (dict, list)) else v for k, v in schema.items()}
    for key in ("anyOf", "oneOf"):
        members = out.get(key)
        if isinstance(members, list) and {"type": "null"} in members:
            rest = [m for m in members if m != {"type": "null"}]
            del out[key]
            if len(rest) == 1 and "$ref" not in rest[0]:
                out = {**rest[0], **out}
            elif len(rest) == 1:
                out["allOf"] = rest
            else:
                out[key] = rest
            out["nullable"] = True
    if isinstance(out.get("type"), list):
        kinds = [t for t in out["type"] if t != "null"]
        if len(kinds) < len(out["type"]):
            out["nullable"] = True
        out["type"] = kinds[0] if len(kinds) == 1 else kinds
    if "const" in out:
        out["enum"] = [out.pop("const")]
    if isinstance(out.get("examples"), list):
        examples = out.pop("examples")
        if examples:
            out["example"] = examples[0]
    for exclusive, bound in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        if isinstance(out.get(exclusive), (int, float)) and not isinstance(out.get(exclusive), bool):
            out[bound] = out.pop(exclusive)
            out[exclusive] = True
    if "$ref" in out and len(out) > 1:
        ref = out.pop("$ref")
        out["allOf"] = [{"$ref": ref}, *out.get("allOf", [])]
    return out


class PydanticSchemas:
    """pydantic models as takibi's schemas: request and response checks, constraints included."""

    validation_problem: Any = ValidationProblem

    def checker(self, tp: Any, target: str) -> _Checker:
        return _Checker(tp, target)

    def response_encoder(self, tp: Any) -> _Encoder:
        return _Encoder(tp)

    def builder(self, openapi31: bool) -> _DocBuilder:
        return _DocBuilder(openapi31)


__all__ = ["PydanticSchemas", "to_openapi30"]
