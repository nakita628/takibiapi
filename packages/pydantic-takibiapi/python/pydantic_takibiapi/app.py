"""``TakibiAPI``: takibi with pydantic models as its schemas."""

from __future__ import annotations

from typing import Any

import takibiapi

from .schemas import PydanticSchemas


class TakibiAPI(takibiapi.TakibiAPI):
    """``takibiapi.TakibiAPI`` whose routes are described with pydantic models (``Model``): requests
    are checked with pydantic, constraints included, before the handler runs (422 when they do
    not match), and ``c.json(...)`` checks the response against the declared model (500 when the
    handler breaks its own contract). Everything else (routing, middleware, ASGI / RSGI, the
    OpenAPI document) is takibi's."""

    schemas: Any = PydanticSchemas()


__all__ = ["TakibiAPI"]
