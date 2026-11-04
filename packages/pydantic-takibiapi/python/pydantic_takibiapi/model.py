"""``Model``: the base of every schema, a pydantic model with the strict settings takibi requires."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    """A strict pydantic model: no type conversion in JSON (``"1"`` is not an ``int``; parameters
    from the path, query, headers and cookies are still read from their text), and no keys the
    model does not declare.

        class Todo(Model):
            id: int = Field(ge=1)
            title: str = Field(min_length=1, max_length=100)
    """

    model_config = ConfigDict(strict=True, extra="forbid")


class ValidationErrorItem(Model):
    in_: Annotated[str, Field(alias="in", description="Where the value was sent: path, query, header, cookie or body")]
    path: Annotated[str, Field(description="Dot-separated path inside that part", examples=["title"])]
    code: Annotated[str, Field(description="The pydantic error type", examples=["string_too_short"])]
    message: Annotated[str, Field(examples=["String should have at least 1 character"])]


class ValidationProblem(Model):
    """RFC 9457 Problem Details for a request part that does not match its schema."""

    type: Annotated[str, Field(examples=["/problems/validation-error"])]
    title: Annotated[str, Field(examples=["Unprocessable Content"])]
    status: Annotated[int, Field(examples=[422])]
    detail: str
    instance: Annotated[str, Field(description="The request path")]
    errors: list[ValidationErrorItem]


__all__ = ["Model", "ValidationErrorItem", "ValidationProblem"]
