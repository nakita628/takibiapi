"""How an app turns the types in its routes into request checks, response checks and OpenAPI
schemas. ``TakibiAPI.schemas`` is a :class:`Schemas`; takibi's own, :class:`TypeSchemas`, works with
Python's types and checks request types only. Other packages (``pydantic-takibiapi``)
provide their own.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from .problems import ValidationProblem
from .schema import Codec, SchemaBuilder, fields, is_object_type, is_sequence, unwrap


class Checker(Protocol):
    """Checks one request part. Raises :class:`takibiapi.ValidationError` when it does not match."""

    # (name, repeats): the keys taken from the query / headers / cookies; ``repeats`` collects
    # every value of a query key (``?tag=a&tag=b``).
    names: list[tuple[str, bool]]

    def decode(self, value: Any, *, coerce: bool) -> Any:
        """``value`` from Python (``coerce``: text from the path, query, headers or cookies)."""
        ...

    def decode_json(self, data: bytes) -> Any:
        """A JSON body; ``ValueError`` for malformed JSON."""
        ...


class Encoder(Protocol):
    """Checks a response body and writes it as JSON; raises :class:`takibiapi.ValidationError`."""

    def encode_json(self, value: Any) -> bytes: ...


class DocBuilder(Protocol):
    """Writes types as OpenAPI schemas, collecting named ones into ``components``."""

    @property
    def components(self) -> dict[str, Any]:
        """The named schemas; read after every ``of`` / ``parameters`` call."""
        ...

    def of(self, tp: Any, io: Literal["input", "output"] = "input") -> dict[str, Any]:
        """``tp``'s schema as a request (``input``) or a response (``output``) has it."""
        ...

    def parameters(self, tp: Any, location: str) -> list[dict[str, Any]]: ...


class Schemas(Protocol):
    validation_problem: Any  # the 422 body type in the document

    def checker(self, tp: Any, target: str) -> Checker: ...

    def response_encoder(self, tp: Any) -> Encoder | None: ...

    def builder(self, openapi31: bool) -> DocBuilder: ...


class _TypeChecker:
    __slots__ = ("decode", "decode_json", "names")

    def __init__(self, tp: Any) -> None:
        codec = Codec(tp)
        self.names = [(f.name, is_sequence(f.type)) for f in fields(tp)] if is_object_type(tp) else []
        self.decode = codec.decode
        self.decode_json = codec.decode_json


class _TypeDocBuilder(SchemaBuilder):
    def of(self, tp: Any, io: Literal["input", "output"] = "input") -> dict[str, Any]:
        return super().of(tp)  # Python's types read and write the same way

    def parameters(self, tp: Any, location: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in fields(tp):
            param: dict[str, Any] = {
                "name": f.name,
                "in": location,
                "required": location == "path" or f.required,
                "schema": self.of(f.type, "input"),
            }
            meta = unwrap(f.type)[1]
            if "description" in meta:
                param["description"] = meta["description"]
            out.append(param)
        return out


class TypeSchemas:
    """Python's own types; request parts are checked against their types only."""

    validation_problem: Any = ValidationProblem

    def checker(self, tp: Any, target: str) -> Checker:
        if target != "json" and not is_object_type(tp):
            raise TypeError(f"request {target} parts must be a dataclass or TypedDict, got {tp!r}")
        return _TypeChecker(tp)

    def response_encoder(self, tp: Any) -> Encoder | None:
        return None  # responses are checked by the type checker only

    def builder(self, openapi31: bool) -> DocBuilder:
        return _TypeDocBuilder(openapi31=openapi31)


__all__ = ["Checker", "DocBuilder", "Encoder", "Schemas", "TypeSchemas"]
