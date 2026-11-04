"""OpenAPI document generation from :class:`Route` configs (``app.doc`` / ``app.getOpenAPIDocument``)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from .route import AnyRoute

if TYPE_CHECKING:
    from .schemas import DocBuilder, Schemas

PARAM_LOCATIONS = (("params", "path"), ("query", "query"), ("headers", "header"), ("cookies", "cookie"))
_OPERATION_KEYS = ("summary", "description", "tags", "operationId", "deprecated", "security")


class OpenAPIConfig(TypedDict, total=False):
    """The document's top level besides ``paths``; ``openapi`` and ``info`` are required."""

    openapi: str  # "3.0.0" ... "3.0.4" or "3.1.0" / "3.1.1": decides how schemas are written
    info: Mapping[str, Any]  # {"title": ..., "version": ...}
    servers: list[Mapping[str, Any]]
    tags: list[Mapping[str, Any]]
    security: list[Mapping[str, list[str]]]
    components: Mapping[str, Any]
    externalDocs: Mapping[str, Any]


def check_config(config: Mapping[str, Any]) -> bool:
    """Whether ``config`` targets OpenAPI 3.1 (else 3.0); raises for a missing or other version."""
    version = config.get("openapi")
    if not isinstance(version, str) or not version.startswith(("3.0.", "3.1.")):
        raise ValueError(f'config["openapi"] must be a 3.0.x or 3.1.x version such as "3.1.0", got {version!r}')
    if "info" not in config:
        raise ValueError('config["info"] is required: {"title": ..., "version": ...}')
    return version.startswith("3.1.")


def build_document(routes: Iterable[type[AnyRoute]], config: Mapping[str, Any], schemas: Schemas) -> dict[str, Any]:
    builder = schemas.builder(check_config(config))
    document: dict[str, Any] = dict(config)
    paths: dict[str, dict[str, Any]] = {}
    for route in routes:
        if route.config.get("hide"):
            continue
        paths.setdefault(route.path, {})[route.method] = _operation(route, builder, schemas.validation_problem)
    document["paths"] = paths
    if builder.components:
        existing = dict(document.get("components") or {})
        existing["schemas"] = {**builder.components, **existing.get("schemas", {})}
        document["components"] = existing
    return document


def _operation(route: type[AnyRoute], builder: DocBuilder, validation_problem: Any) -> dict[str, Any]:
    config = route.config
    operation: dict[str, Any] = {k: config[k] for k in _OPERATION_KEYS if k in config}
    request = config.get("request") or {}
    parameters: list[dict[str, Any]] = []
    for key, location in PARAM_LOCATIONS:
        if request.get(key) is not None:
            parameters.extend(builder.parameters(request[key], location))
    if parameters:
        operation["parameters"] = parameters
    body = request.get("body")
    if body:
        request_body: dict[str, Any] = {"content": _content(body.get("content", {}), builder, "input")}
        for key in ("description", "required"):
            if key in body:
                request_body[key] = body[key]
        operation["requestBody"] = request_body
    responses: dict[str, Any] = {}
    declared = {str(status) for status in config.get("responses", {})}
    if (parameters or body) and "422" not in declared:
        # What takibi answers when a request part does not match its type.
        responses["422"] = {
            "description": "A request part does not match the operation's schema",
            "content": _content({"application/problem+json": {"schema": validation_problem}}, builder, "output"),
        }
    for status, response in config.get("responses", {}).items():
        item: dict[str, Any] = {"description": response.get("description", "")}
        if response.get("headers"):
            item["headers"] = response["headers"]
        if response.get("content"):
            item["content"] = _content(response["content"], builder, "output")
        responses[str(status)] = item
    operation["responses"] = responses
    return operation


def _content(content: Mapping[str, Any], builder: DocBuilder, io: Literal["input", "output"]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for media_type, media in content.items():
        entry = {k: v for k, v in media.items() if k != "schema"}
        if media.get("schema") is not None:
            entry["schema"] = builder.of(media["schema"], io)
        out[media_type] = entry
    return out
