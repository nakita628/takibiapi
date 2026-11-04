"""takibi-swagger-ui: Swagger UI for a takibiapi app, as Hono's ``@hono/swagger-ui``.

    from takibi_swagger_ui import swaggerUI

    app.doc("/doc", {"openapi": "3.1.0", "info": {"title": "My API", "version": "1.0.0"}})
    app.use(swaggerUI({"url": "/doc"}), prefix="/ui")

``GET /ui`` answers the Swagger UI page, which loads ``swagger-ui-dist`` from jsDelivr and the
document from ``url``. Other methods pass through to the app.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable, Sequence
from typing import Any, TypedDict

from takibiapi import Context, Middleware, Next, Response


class Asset(TypedDict):
    css: list[str]
    js: list[str]


class SwaggerUIOptions(TypedDict, total=False):
    """``title`` and ``version`` are this package's; the other keys are Swagger UI's
    configuration (https://swagger.io/docs/open-source-tools/swagger-ui/usage/configuration/)."""

    title: str  # <title> of the page (default "SwaggerUI")
    version: str  # swagger-ui-dist version on jsDelivr (default: latest)
    manuallySwaggerUIHtml: Callable[[Asset], str]  # the page body, built from the asset URLs
    url: str
    urls: list[dict[str, str]]
    spec: dict[str, Any]
    configUrl: str
    layout: str
    docExpansion: str
    deepLinking: bool
    displayOperationId: bool
    defaultModelsExpandDepth: int
    defaultModelExpandDepth: int
    defaultModelRendering: str
    displayRequestDuration: bool
    filter: bool | str
    maxDisplayedTags: int
    showExtensions: bool
    showCommonExtensions: bool
    queryConfigEnabled: bool
    persistAuthorization: bool
    tryItOutEnabled: bool
    requestSnippetsEnabled: bool
    requestSnippets: dict[str, Any]
    syntaxHighlight: bool | dict[str, Any]
    supportedSubmitMethods: list[str]
    validatorUrl: str | None
    withCredentials: bool
    oauth2RedirectUrl: str
    # JavaScript, written into the page as it is:
    presets: Sequence[str]  # e.g. ["SwaggerUIBundle.presets.apis", "SwaggerUIStandalonePreset"]
    plugins: Sequence[str]
    requestInterceptor: str  # e.g. "(req) => req"
    responseInterceptor: str
    onComplete: str
    operationsSorter: str
    tagsSorter: str
    modelPropertyMacro: str
    parameterMacro: str


# Keys whose values are JavaScript source (a list of expressions, or one expression) rather than data.
_JS_LISTS = frozenset({"presets", "plugins"})
_JS = frozenset(
    {
        "requestInterceptor",
        "responseInterceptor",
        "onComplete",
        "operationsSorter",
        "tagsSorter",
        "modelPropertyMacro",
        "parameterMacro",
    }
)
_OWN = frozenset({"title", "version", "manuallySwaggerUIHtml"})


def remote_assets(version: str | None = None) -> Asset:
    """The swagger-ui-dist stylesheet and script on jsDelivr."""
    base = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist{'@' + version if version else ''}"
    return {"css": [f"{base}/swagger-ui.css"], "js": [f"{base}/swagger-ui-bundle.js"]}


def _js_value(value: Any) -> str:
    # JSON inside <script>: "<" escaped so a value cannot close the tag.
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def render_options(options: SwaggerUIOptions) -> str:
    """The options as the properties of the ``SwaggerUIBundle({...})`` object literal."""
    parts: list[str] = []
    items: dict[str, Any] = dict(options)
    for key, value in items.items():
        if key in _OWN:
            continue
        if value is None:
            if key == "validatorUrl":  # null turns the validator badge off
                parts.append("validatorUrl: null")
            continue
        if key in _JS_LISTS:
            rendered = "[" + ",".join(str(v) for v in value) + "]"
        elif key in _JS and isinstance(value, str):
            rendered = value
        else:
            rendered = _js_value(value)
        parts.append(f"{key}: {rendered}")
    return ",\n            ".join(parts)


def SwaggerUI(options: SwaggerUIOptions) -> str:  # noqa: N802
    """The Swagger UI markup (the page's body)."""
    asset = remote_assets(options.get("version"))
    manual = options.get("manuallySwaggerUIHtml")
    if manual is not None:
        return manual(asset)
    links = "".join(f'<link rel="stylesheet" href="{html.escape(u)}" />' for u in asset["css"])
    scripts = "".join(f'<script src="{html.escape(u)}" crossorigin="anonymous"></script>' for u in asset["js"])
    rendered = render_options(options)
    return f"""<div>
      <div id="swagger-ui"></div>
      {links}
      {scripts}
      <script>
        window.onload = () => {{
          window.ui = SwaggerUIBundle({{
            dom_id: '#swagger-ui',{"" if not rendered else chr(10) + "            " + rendered},
          }})
        }}
      </script>
    </div>"""


def swaggerUI(options: SwaggerUIOptions) -> Middleware:  # noqa: N802
    """The Swagger UI page as middleware: ``app.use(swaggerUI({"url": "/doc"}), prefix="/ui")``."""
    title = html.escape(options.get("title", "SwaggerUI"))
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        '<meta name="description" content="SwaggerUI" />'
        f"<title>{title}</title></head><body>{SwaggerUI(options)}</body></html>"
    ).encode()

    async def middleware(c: Context[Any], next: Next) -> Response:
        if c.req.method not in ("GET", "HEAD"):
            return await next()
        return Response(page, 200, {"content-type": "text/html; charset=utf-8"})

    return middleware


__all__ = ["Asset", "SwaggerUI", "SwaggerUIOptions", "remote_assets", "render_options", "swaggerUI"]
__version__ = "0.1.0"
