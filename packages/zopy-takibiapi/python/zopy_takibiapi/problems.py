"""The RFC 9457 Problem Details body answered when a request part does not match its schema."""

from __future__ import annotations

from typing import Any, cast

from zopy import z

# "in" is a Python keyword, so this one shape is built from a namespace instead of a class body.
ValidationErrorItem: Any = cast(Any, type(z.StrictObject))(
    "ValidationErrorItem",
    (z.StrictObject,),
    {
        "in": z.enum(("path", "query", "header", "cookie", "body")).openapi(
            {"description": "Where the value was sent (an OpenAPI parameter location, or body)"}
        ),
        "path": z.string().openapi({"description": "Dot-separated path inside that part", "example": "title"}),
        "code": z.string().openapi({"description": "The Zod issue code", "example": "too_small"}),
        "message": z.string().openapi({"example": "Too small: expected string to have >=1 characters"}),
    },
).openapi("ValidationErrorItem")


class ValidationProblemFields(z.StrictObject):
    type = z.literal("/problems/validation-error")
    title = z.literal("Unprocessable Content")
    status = z.literal(422)
    detail = z.string()
    instance = z.string().openapi({"description": "The request path"})
    errors = z.array(ValidationErrorItem)


ValidationProblem = ValidationProblemFields.openapi(
    "ValidationProblem",
    {"description": "RFC 9457 Problem Details for a request part that does not match its schema."},
)

__all__ = ["ValidationErrorItem", "ValidationProblem"]
