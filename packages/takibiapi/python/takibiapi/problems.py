"""The RFC 9457 Problem Details body takibi answers with when a request part has the wrong type.

Operations that check a request document this 422 response automatically unless they declare 422.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

# "in" is a Python keyword, so this one shape is written in the functional form.
ValidationErrorItem = TypedDict(
    "ValidationErrorItem",
    {
        "in": Annotated[
            Literal["path", "query", "header", "cookie", "body"],
            {"description": "Where the value was sent (an OpenAPI parameter location, or body)"},
        ],
        "path": Annotated[str, {"description": "Dot-separated path inside that part", "example": "todoId"}],
        "code": Annotated[
            Literal["invalid_type", "invalid_format", "invalid_value", "invalid_union", "missing"],
            {"example": "invalid_type"},
        ],
        "message": Annotated[str, {"example": "Expected integer, received str"}],
    },
)


class ValidationProblem(TypedDict):
    type: Literal["/problems/validation-error"]
    title: Literal["Unprocessable Content"]
    status: Literal[422]
    detail: str
    instance: Annotated[str, {"description": "The request path"}]
    errors: list[ValidationErrorItem]
