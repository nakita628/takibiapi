"""zopy-takibiapi: takibi with zopy (Zod for Python) schemas, checked strictly.

    from zopy_takibiapi import Context, Response, TakibiAPI, createRoute, z

Schemas are zopy's (``z.StrictObject`` classes, ``z.string().min(1)``, ...). An object is named for
the document where it is declared, ``class Todo(OpenAPIObject, openapi="Todo")``, and a field
carries its metadata, ``z.int().openapi({"example": 1})``. Requests are parsed with them before the handler runs, checks
included, and responses are encoded with them; every object must reject unknown keys.
"""

from zopy import z

from takibiapi import (
    Context,
    Middleware,
    Next,
    OpenAPIConfig,
    Request,
    RequestConfig,
    Response,
    ResponseConfig,
    ResponseValidationError,
    Route,
    RouteConfig,
    StatusCode,
    TypedResponse,
    ValidationError,
    createRoute,
    problem,
)
from zopy_takibiapi.app import TakibiAPI
from zopy_takibiapi.object import OpenAPIObject
from zopy_takibiapi.problems import ValidationErrorItem, ValidationProblem
from zopy_takibiapi.schemas import ZopySchemas

__all__ = [
    "Context",
    "Middleware",
    "Next",
    "OpenAPIConfig",
    "OpenAPIObject",
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
    "ValidationErrorItem",
    "ValidationProblem",
    "ZopySchemas",
    "createRoute",
    "problem",
    "z",
]
__version__ = "0.2.0"
