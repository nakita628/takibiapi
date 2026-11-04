"""takibi: REST APIs whose OpenAPI document, request type checks and handler types come from
Python's own types.

    from takibiapi import Context, TakibiAPI, createRoute

Routes are declared with ``createRoute`` (dataclasses, ``TypedDict``, ``Literal``, ``Enum``,
``Annotated``, ...) and registered with ``app.openapi(route, handler)``; ``app.doc("/doc", {...})``
serves the OpenAPI document. Request parts are checked against their types only and read with
``c.req.valid(...)``; ``Annotated`` keywords are documentation and responses are not checked.
"""

from takibiapi.app import Fallback, FallbackHandler, FallbackMiddleware, FallbackNext, Middleware, Next, TakibiAPI
from takibiapi.context import Context, Request, Response, ResponseValidationError, TypedResponse, problem
from takibiapi.openapi import OpenAPIConfig
from takibiapi.problems import ValidationProblem
from takibiapi.route import RequestConfig, ResponseConfig, Route, RouteConfig, StatusCode, createRoute
from takibiapi.schema import ValidationError

__all__ = [
    "Context",
    "Fallback",
    "FallbackHandler",
    "FallbackMiddleware",
    "FallbackNext",
    "Middleware",
    "Next",
    "OpenAPIConfig",
    "Request",
    "RequestConfig",
    "Response",
    "ResponseConfig",
    "ResponseValidationError",
    "Route",
    "RouteConfig",
    "StatusCode",
    "TakibiAPI",
    "TypedResponse",
    "ValidationError",
    "ValidationProblem",
    "createRoute",
    "problem",
]
__version__ = "0.1.1"
