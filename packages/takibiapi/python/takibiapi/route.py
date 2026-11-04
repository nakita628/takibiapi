"""``createRoute``: an OpenAPI operation described with Python types.

The types are written to the OpenAPI document, request parts are checked against them (types
only: ``c.req.valid(...)``), and they type the handler: ``createRoute`` returns
``type[Route[P, Q, J, H, R, S]]`` (path params, query, JSON body, headers, response bodies, response
statuses), and a handler for the route takes ``c: Context[thatRoute]``, so ``c.req.valid("param")``
is ``P``, ``c.json(...)`` takes ``R`` with a status in ``S``, and the handler must return what
``c.json`` / ``c.text`` / ``c.body`` build. Responses are not checked at run time.
"""

from __future__ import annotations

import inspect
import types
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, NotRequired, Protocol, TypedDict, TypeVar, overload

if TYPE_CHECKING:  # typing.ReadOnly is 3.13+; annotations are never evaluated at run time
    from typing_extensions import ReadOnly

# Request parts, read with c.req.valid(...): "param", "query", "json", "header".
P = TypeVar("P")
Q = TypeVar("Q")
J = TypeVar("J")
H = TypeVar("H")
# Response bodies the route declares (a union over its ``responses``) -> ``c.json(...)``. Covariant
# (with ReadOnly fields below) so several responses infer ``Todo | Problem``.
R = TypeVar("R", covariant=True)
# Response statuses the route declares: ``Literal[200, 404]`` (with ``"default"`` when declared). The
# responses' keys are inferred as this union when they are literals of ``StatusCode``; ``Route``
# takes it contravariantly, so ``c.json(..., status)`` accepts a status only when the route declares
# it (and any status when the route declares ``"default"``).
K = TypeVar("K")
S = TypeVar("S", contravariant=True)

Method = Literal["get", "post", "put", "patch", "delete", "head", "options", "trace"]

# The HTTP status codes of the IANA registry (and 418). A response key outside them, or one typed
# ``int``, is a type error: annotate shared responses with their codes,
# ``errors: Mapping[Literal[400, 404], ResponseConfig[Problem]]``.
StatusCode = Literal[
    100, 101, 102, 103,
    200, 201, 202, 203, 204, 205, 206, 207, 208, 226,
    300, 301, 302, 303, 304, 305, 307, 308,
    400, 401, 402, 403, 404, 405, 406, 407, 408, 409, 410, 411, 412, 413, 414, 415, 416, 417, 418,
    421, 422, 423, 424, 425, 426, 428, 429, 431, 451,
    500, 501, 502, 503, 504, 505, 506, 507, 508, 510, 511,
]  # fmt: skip

_T_co = TypeVar("_T_co", covariant=True)


class SchemaLike(Protocol[_T_co]):
    """A schema object whose ``parse`` gives ``T`` (zopy's ``z.array(Todo)``, ...): its value type
    types ``c.req.valid(...)`` and ``c.json(...)`` as a class does."""

    def parse(self, data: Any, /) -> _T_co: ...


class MediaType(TypedDict, Generic[J], total=False):
    # A type: ``NewTodo``, ``list[NewTodo]``, ... (a union such as ``A | B`` is accepted too, but
    # the type checker cannot infer the body type from it).
    schema: type[J] | SchemaLike[J] | types.UnionType
    example: Any
    examples: Mapping[str, Any]


class RequestBody(TypedDict, Generic[J], total=False):
    content: Mapping[str, MediaType[J]]
    description: str
    required: bool


class RequestConfig(TypedDict, Generic[P, Q, J, H], total=False):
    # A dataclass or TypedDict whose fields are the parameters.
    params: type[P]
    query: type[Q]
    headers: type[H]
    cookies: type
    body: RequestBody[J]


class ResponseMediaType(TypedDict, Generic[R], total=False):
    schema: ReadOnly[type[R] | SchemaLike[R] | types.UnionType]
    example: ReadOnly[Any]
    examples: ReadOnly[Mapping[str, Any]]


class ResponseConfig(TypedDict, Generic[R], total=False):
    """One entry of ``responses``. Annotate shared ones with their statuses and body type, e.g.
    ``errors: Mapping[Literal[400, 404], ResponseConfig[Problem]]``, and spread them:
    ``"responses": {**errors, 200: {...}}``."""

    description: ReadOnly[str]
    content: ReadOnly[Mapping[str, ResponseMediaType[R]]]
    headers: ReadOnly[Mapping[str, Any]]


class _OperationConfig(TypedDict, Generic[R, K]):
    method: Method
    path: str  # "/todos/{todoId}"
    responses: Mapping[K, ResponseConfig[R]]  # status code (or "default") -> response
    summary: NotRequired[str]
    description: NotRequired[str]
    tags: NotRequired[list[str]]
    operationId: NotRequired[str]
    deprecated: NotRequired[bool]
    security: NotRequired[list[Mapping[str, list[str]]]]
    hide: NotRequired[bool]  # served, but left out of the OpenAPI document


class RouteConfig(_OperationConfig[R, K], Generic[P, Q, J, H, R, K]):
    request: NotRequired[RequestConfig[P, Q, J, H]]


# -- generated: one request shape per combination of params / query / headers / body ------- #
# A TypedDict key that is absent from the dict literal leaves its type variable unsolved. With
# every key of the matching shape required, each part present is solved and the rest are ``Any``.


class _RequestP(TypedDict, Generic[P]):
    params: type[P]
    cookies: NotRequired[type]


class _RouteP(_OperationConfig[R, K], Generic[P, R, K]):
    request: _RequestP[P]


class _RequestQ(TypedDict, Generic[Q]):
    query: type[Q]
    cookies: NotRequired[type]


class _RouteQ(_OperationConfig[R, K], Generic[Q, R, K]):
    request: _RequestQ[Q]


class _RequestH(TypedDict, Generic[H]):
    headers: type[H]
    cookies: NotRequired[type]


class _RouteH(_OperationConfig[R, K], Generic[H, R, K]):
    request: _RequestH[H]


class _RequestB(TypedDict, Generic[J]):
    body: RequestBody[J]
    cookies: NotRequired[type]


class _RouteB(_OperationConfig[R, K], Generic[J, R, K]):
    request: _RequestB[J]


class _RequestPQ(TypedDict, Generic[P, Q]):
    params: type[P]
    query: type[Q]
    cookies: NotRequired[type]


class _RoutePQ(_OperationConfig[R, K], Generic[P, Q, R, K]):
    request: _RequestPQ[P, Q]


class _RequestPH(TypedDict, Generic[P, H]):
    params: type[P]
    headers: type[H]
    cookies: NotRequired[type]


class _RoutePH(_OperationConfig[R, K], Generic[P, H, R, K]):
    request: _RequestPH[P, H]


class _RequestPB(TypedDict, Generic[P, J]):
    params: type[P]
    body: RequestBody[J]
    cookies: NotRequired[type]


class _RoutePB(_OperationConfig[R, K], Generic[P, J, R, K]):
    request: _RequestPB[P, J]


class _RequestQH(TypedDict, Generic[Q, H]):
    query: type[Q]
    headers: type[H]
    cookies: NotRequired[type]


class _RouteQH(_OperationConfig[R, K], Generic[Q, H, R, K]):
    request: _RequestQH[Q, H]


class _RequestQB(TypedDict, Generic[Q, J]):
    query: type[Q]
    body: RequestBody[J]
    cookies: NotRequired[type]


class _RouteQB(_OperationConfig[R, K], Generic[Q, J, R, K]):
    request: _RequestQB[Q, J]


class _RequestHB(TypedDict, Generic[J, H]):
    headers: type[H]
    body: RequestBody[J]
    cookies: NotRequired[type]


class _RouteHB(_OperationConfig[R, K], Generic[J, H, R, K]):
    request: _RequestHB[J, H]


class _RequestPQH(TypedDict, Generic[P, Q, H]):
    params: type[P]
    query: type[Q]
    headers: type[H]
    cookies: NotRequired[type]


class _RoutePQH(_OperationConfig[R, K], Generic[P, Q, H, R, K]):
    request: _RequestPQH[P, Q, H]


class _RequestPQB(TypedDict, Generic[P, Q, J]):
    params: type[P]
    query: type[Q]
    body: RequestBody[J]
    cookies: NotRequired[type]


class _RoutePQB(_OperationConfig[R, K], Generic[P, Q, J, R, K]):
    request: _RequestPQB[P, Q, J]


class _RequestPHB(TypedDict, Generic[P, J, H]):
    params: type[P]
    headers: type[H]
    body: RequestBody[J]
    cookies: NotRequired[type]


class _RoutePHB(_OperationConfig[R, K], Generic[P, J, H, R, K]):
    request: _RequestPHB[P, J, H]


class _RequestQHB(TypedDict, Generic[Q, J, H]):
    query: type[Q]
    headers: type[H]
    body: RequestBody[J]
    cookies: NotRequired[type]


class _RouteQHB(_OperationConfig[R, K], Generic[Q, J, H, R, K]):
    request: _RequestQHB[Q, J, H]


class _RequestPQHB(TypedDict, Generic[P, Q, J, H]):
    params: type[P]
    query: type[Q]
    headers: type[H]
    body: RequestBody[J]
    cookies: NotRequired[type]


class _RoutePQHB(_OperationConfig[R, K], Generic[P, Q, J, H, R, K]):
    request: _RequestPQHB[P, Q, J, H]


class Route(Generic[P, Q, J, H, R, S]):
    """An operation made by :func:`createRoute`. Declare it as a class, so that handlers can name
    it::

        class getTodoRoute(createRoute({...})):
            '''Get one todo.'''         # the operation description unless one is given


        def getTodoRouteHandler(c: Context[getTodoRoute]): ...
    """

    config: ClassVar[dict[str, Any]] = {}
    method: ClassVar[str] = ""
    path: ClassVar[str] = ""
    # True on the bare class createRoute returns; False on ``class x(createRoute(...))``.
    _generated: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "_generated" not in cls.__dict__:
            cls._generated = False
        doc = cls.__dict__.get("__doc__")
        if doc and "description" not in cls.config:
            cls.config = {**cls.config, "description": inspect.cleandoc(doc)}

    def __init__(self) -> None:
        raise TypeError("routes are classes: use the route itself (app.openapi(route, handler))")


AnyRoute = Route[Any, Any, Any, Any, Any, Any]


def route_class(config: Mapping[str, Any], *, generated: bool = True) -> type[AnyRoute]:
    method, path = str(config["method"]).lower(), str(config["path"])
    namespace = {"config": dict(config), "method": method, "path": path, "__doc__": None, "_generated": generated}
    return type(f"Route[{method.upper()} {path}]", (Route,), namespace)


# Function-scoped copies of the type variables, for the overloads.
_P = TypeVar("_P")
_Q = TypeVar("_Q")
_J = TypeVar("_J")
_H = TypeVar("_H")
_R = TypeVar("_R")
_K = TypeVar("_K", bound=StatusCode | Literal["default"])


@overload
def createRoute(config: _OperationConfig[_R, _K]) -> type[Route[Any, Any, Any, Any, _R, _K]]: ...


# A dict literal with a "request" key cannot match the overload above (TypedDicts reject extra
# keys), although pyright's overlap check treats the shapes below as its structural subtypes.
@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RouteP[_P, _R, _K],
) -> type[Route[_P, Any, Any, Any, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RouteQ[_Q, _R, _K],
) -> type[Route[Any, _Q, Any, Any, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RouteH[_H, _R, _K],
) -> type[Route[Any, Any, Any, _H, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RouteB[_J, _R, _K],
) -> type[Route[Any, Any, _J, Any, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RoutePQ[_P, _Q, _R, _K],
) -> type[Route[_P, _Q, Any, Any, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RoutePH[_P, _H, _R, _K],
) -> type[Route[_P, Any, Any, _H, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RoutePB[_P, _J, _R, _K],
) -> type[Route[_P, Any, _J, Any, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RouteQH[_Q, _H, _R, _K],
) -> type[Route[Any, _Q, Any, _H, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RouteQB[_Q, _J, _R, _K],
) -> type[Route[Any, _Q, _J, Any, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RouteHB[_J, _H, _R, _K],
) -> type[Route[Any, Any, _J, _H, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RoutePQH[_P, _Q, _H, _R, _K],
) -> type[Route[_P, _Q, Any, _H, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RoutePQB[_P, _Q, _J, _R, _K],
) -> type[Route[_P, _Q, _J, Any, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RoutePHB[_P, _J, _H, _R, _K],
) -> type[Route[_P, Any, _J, _H, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RouteQHB[_Q, _J, _H, _R, _K],
) -> type[Route[Any, _Q, _J, _H, _R, _K]]: ...


@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: _RoutePQHB[_P, _Q, _J, _H, _R, _K],
) -> type[Route[_P, _Q, _J, _H, _R, _K]]: ...


# Anything else (cookies only, a dict built at run time).
@overload
def createRoute(  # pyright: ignore[reportOverlappingOverload]
    config: RouteConfig[_P, _Q, _J, _H, _R, _K],
) -> type[Route[_P, _Q, _J, _H, _R, _K]]: ...


def createRoute(config: Any) -> Any:  # noqa: N802 (the conventional name)
    """Define an operation. Returns a route class to subclass:
    ``class getTodoRoute(createRoute({...})): ...``."""
    return route_class(config)


__all__ = [
    "AnyRoute",
    "RequestConfig",
    "ResponseConfig",
    "Route",
    "RouteConfig",
    "SchemaLike",
    "StatusCode",
    "createRoute",
]
