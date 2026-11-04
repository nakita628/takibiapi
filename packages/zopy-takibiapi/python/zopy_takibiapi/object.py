"""``OpenAPIObject``: a ``z.StrictObject`` named for the OpenAPI document where it is declared.

    class Todo(OpenAPIObject, openapi="Todo"):
        id = z.int().openapi({"example": 1})

is ``@hono/zod-openapi``'s ``z.object({...}).openapi("Todo")`` as a class: the class itself is the
component (``components.schemas.Todo``, referenced with ``$ref``), so it is both the schema a route
declares and the type of the values its handler returns. ``Todo.openapi("Todo")`` names a copy of
the class instead, which leaves two names for one schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from zopy import z
from zopy.registries import globalRegistry

if TYPE_CHECKING:
    from zopy.core import ZodType


def _metadata(openapi: str | Mapping[str, Any], name: str) -> dict[str, Any]:
    if isinstance(openapi, str):
        if not openapi:
            raise TypeError(f"{name}: openapi= must name the component, got an empty string")
        return {"id": openapi}
    if not isinstance(openapi, Mapping):
        raise TypeError(f"{name}: openapi= is a component name or a metadata mapping, got {openapi!r}")
    meta = dict(openapi)
    if "param" in meta:
        raise TypeError(f"{name}: openapi= cannot take 'param'; parameter metadata goes on the fields")
    return meta


class OpenAPIObject(z.StrictObject):
    """``z.StrictObject`` with the OpenAPI metadata in its declaration.

    ``openapi="Todo"`` names the component; ``openapi={"id": "Todo", "description": ...}`` names it
    and adds metadata, which a mapping without ``"id"`` adds alone (the schema is then written in
    place). Subclasses are unnamed until they say otherwise: a name belongs to one class.
    """

    def __init_subclass__(
        cls,
        *,
        openapi: str | Mapping[str, Any] | None = None,
        strict: bool = False,
        loose: bool = False,
        catchall: ZodType[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # strict / loose / catchall are ObjectMeta's (it takes them out before type.__new__ runs);
        # they are in the signature so that type checkers accept them next to openapi=.
        super().__init_subclass__(**kwargs)
        if openapi is not None:
            # The registry is keyed by the class itself, so the class is the named schema.
            globalRegistry.add(cls, _metadata(openapi, cls.__name__))


__all__ = ["OpenAPIObject"]
