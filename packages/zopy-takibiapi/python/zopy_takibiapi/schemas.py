"""``ZopySchemas``: zopy (Zod for Python) schemas as takibi's schemas (see ``takibiapi.schemas``).

Requests are parsed with the route's schemas exactly as Zod parses: checks included, no
conversion unless the schema asks for it (``z.coerce.number()`` for a numeric path parameter), and
no unknown keys (every object must be ``z.StrictObject``). Responses are encoded with the schema
the route declares for the status (``safeEncode``: checked, and codecs turned into their wire
form) before they are written. As in Zod, a named schema (``class Todo(OpenAPIObject, openapi="Todo")``,
or ``.openapi("Todo")``) is written once to ``components.schemas`` and referenced with ``$ref``;
other schemas are written in place.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Literal

from zopy import z
from zopy.core import ZodType
from zopy.declarative import Object, ObjectMeta
from zopy.registries import globalRegistry, openapiParamRegistry

from takibiapi import ValidationError
from takibiapi.schema import dumps, loads

from .problems import ValidationProblem

COMPONENT_URI = "#/components/schemas/{}"


def is_schema(tp: Any) -> bool:
    return isinstance(tp, (ZodType, ObjectMeta))


def _children(value: Any) -> Iterator[Any]:
    """The schemas directly inside ``value``'s definition (object fields, array items, ...)."""
    if isinstance(value, ObjectMeta):
        yield from value.shape.values()
    for item in value._def.values():
        if is_schema(item):
            yield item
        elif isinstance(item, (list, tuple)):
            yield from (i for i in item if is_schema(i))
        elif isinstance(item, Mapping):
            yield from (i for i in item.values() if is_schema(i))


def strict_error(schema: Any) -> str | None:
    """Why ``schema`` is not strict enough, or None: every object must reject unknown keys."""
    seen: set[int] = set()
    stack = [schema]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        definition = current._def
        if definition.get("type") == "object" and not isinstance(definition.get("catchall"), z.ZodNever):
            name = current.__name__ if isinstance(current, type) else type(current).__name__
            return f"{name} is not strict: unknown keys must be an error (subclass z.StrictObject)"
        stack.extend(_children(current))
    return None


def _checked(tp: Any, target: str) -> Any:
    if not is_schema(tp):
        raise TypeError(f"request {target} / response schemas must be zopy schemas, got {tp!r}")
    error = strict_error(tp)
    if error is not None:
        raise TypeError(error)
    return tp


def _issues(error: Any) -> list[dict[str, Any]]:
    return [{"path": list(i["path"]), "code": i["code"], "message": i["message"]} for i in error.issues]


def _unwrapped(schema: Any) -> Any:
    """The schema inside ``.optional()`` / ``.nullable()`` / ``.default()``."""
    while not isinstance(schema, ObjectMeta) and "innerType" in schema._def:
        schema = schema._def["innerType"]
    return schema


class _Checker:
    __slots__ = ("_schema", "names")

    def __init__(self, tp: Any, target: str) -> None:
        self._schema = _checked(tp, target)
        if target == "json":
            self.names: list[tuple[str, bool]] = []
            return
        if not isinstance(tp, ObjectMeta):
            raise TypeError(f"request {target} parts must be an object schema (z.StrictObject), got {tp!r}")
        self.names = [(name, isinstance(_unwrapped(field), z.ZodArray)) for name, field in tp.shape.items()]

    def decode(self, value: Any, *, coerce: bool) -> Any:
        # Parameters arrive as text and are parsed as the schema says (z.coerce converts), as in Zod.
        result = self._schema.safeParse(value)
        if not result.success:
            raise ValidationError(_issues(result.error))
        return result.data

    def decode_json(self, data: bytes) -> Any:
        return self.decode(loads(data), coerce=False)  # loads raises ValueError for malformed JSON


def _wire(value: Any) -> Any:
    """``dumps``'s ``default`` for an encoded result: a z.Object instance as its wire dict (the
    JSON writer then writes its values, calling this again for nested objects)."""
    if isinstance(value, Object):
        # Without optional fields to leave out, the instance's own dict is the wire dict.
        return value._wire_dict() if type(value)._absent_when_none() else vars(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class _Encoder:
    __slots__ = ("_schema",)

    def __init__(self, tp: Any) -> None:
        self._schema = _checked(tp, "response")

    def encode_json(self, value: Any) -> bytes:
        result = self._schema.safeEncode(value)
        if not result.success:
            raise ValidationError(_issues(result.error))
        return dumps(result.data, default=_wire)


class _DocBuilder:
    """Schemas as zopy's ``toJSONSchema`` writes them, named ones (``openapi="Todo"``) collected
    into ``components``. Bodies are documented in their wire form (``io: "input"``, a codec's JSON
    side) both ways: requests are parsed from it and responses encoded into it."""

    def __init__(self, openapi31: bool) -> None:
        self.openapi31 = openapi31
        self.components: dict[str, Any] = {}

    def of(self, tp: Any, io: Literal["input", "output"] = "input") -> dict[str, Any]:
        params = {
            "target": "draft-2020-12" if self.openapi31 else "openapi-3.0",
            "io": "input",
            "unrepresentable": "any",
            "uri": COMPONENT_URI.format,
        }
        out = z.toJSONSchema(tp, params)
        out.pop("$schema", None)
        for key in ("$defs", "definitions"):
            for name, schema in out.pop(key, {}).items():
                known = self.components.setdefault(name, schema)
                if known != schema:
                    raise TypeError(f"two different schemas are named {name!r}")
        return out

    def parameters(self, tp: Any, location: str) -> list[dict[str, Any]]:
        if not isinstance(tp, ObjectMeta):
            raise TypeError(f"{location} parameters must be an object schema, got {tp!r}")
        out: list[dict[str, Any]] = []
        for name, field in tp.shape.items():
            param: dict[str, Any] = {
                "name": name,
                "in": location,
                "required": location == "path" or not field.isOptional(),
                "schema": self.of(field),
            }
            meta = globalRegistry.get(field) or {}
            if "description" in meta:
                param["description"] = meta["description"]
            param.update(openapiParamRegistry.get(field) or {})
            out.append(param)
        return out


class ZopySchemas:
    """zopy schemas as takibi's schemas: request and response checks, Zod's semantics."""

    validation_problem: Any = ValidationProblem

    def checker(self, tp: Any, target: str) -> _Checker:
        return _Checker(tp, target)

    def response_encoder(self, tp: Any) -> _Encoder:
        return _Encoder(tp)

    def builder(self, openapi31: bool) -> _DocBuilder:
        return _DocBuilder(openapi31)


__all__ = ["ZopySchemas", "is_schema", "strict_error"]
