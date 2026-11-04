"""``TakibiAPI``: takibi with zopy (Zod for Python) schemas."""

from __future__ import annotations

from typing import Any

import takibiapi

from .schemas import ZopySchemas


class TakibiAPI(takibiapi.TakibiAPI):
    """``takibiapi.TakibiAPI`` whose routes are described with zopy schemas: requests are parsed with
    them before the handler runs (422 when they do not match), and ``c.json(...)`` encodes the
    response with the declared schema (500 when the handler breaks its own contract). Everything
    else (routing, middleware, ASGI / RSGI, the OpenAPI document) is takibi's."""

    schemas: Any = ZopySchemas()


__all__ = ["TakibiAPI"]
