"""pydantic-takibiapi: takibi with pydantic models as the schemas, checked strictly.

    from pydantic_takibiapi import Context, Model, Response, TakibiAPI, createRoute

Schemas are :class:`Model` subclasses (pydantic models with ``strict=True`` and
``extra="forbid"``). Requests are checked with pydantic before the handler runs, ``Field``
constraints included; responses are checked against the declared model; the OpenAPI document
comes from pydantic's JSON Schema.
"""

from pydantic_takibiapi.app import TakibiAPI
from pydantic_takibiapi.model import Model, ValidationErrorItem, ValidationProblem
from pydantic_takibiapi.schemas import PydanticSchemas
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
    ValidationError,
    createRoute,
    problem,
)

__all__ = [
    "Context",
    "Middleware",
    "Model",
    "Next",
    "OpenAPIConfig",
    "PydanticSchemas",
    "Request",
    "RequestConfig",
    "Response",
    "ResponseConfig",
    "ResponseValidationError",
    "Route",
    "RouteConfig",
    "TakibiAPI",
    "ValidationError",
    "ValidationErrorItem",
    "ValidationProblem",
    "createRoute",
    "problem",
]
__version__ = "0.1.1"
