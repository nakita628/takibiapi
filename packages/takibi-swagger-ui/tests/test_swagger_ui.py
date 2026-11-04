from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import pytest

from takibi_swagger_ui import SwaggerUI, remote_assets, render_options, swaggerUI
from takibiapi import Context, TakibiAPI, createRoute


@dataclass
class Message:
    message: str


class getMessageRoute(
    createRoute(
        {
            "method": "get",
            "path": "/message",
            "responses": {200: {"description": "A message", "content": {"application/json": {"schema": Message}}}},
        }
    )
):
    """Get a message."""


def getMessageRouteHandler(c: Context[getMessageRoute]):
    return c.json(Message("hi"))


def make_app() -> TakibiAPI:
    app = TakibiAPI().openapi(getMessageRoute, getMessageRouteHandler)
    app.doc("/doc", {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}})
    app.use(swaggerUI({"url": "/doc", "title": "My <API>"}), prefix="/ui")
    return app


def test_serves_the_page() -> None:
    res = asyncio.run(make_app().request("/ui"))
    assert res.status == 200
    assert res.headers["content-type"] == "text/html; charset=utf-8"
    page = res.text()
    assert page.startswith("<!doctype html>")
    assert "<title>My &lt;API&gt;</title>" in page
    assert 'href="https://cdn.jsdelivr.net/npm/swagger-ui-dist/swagger-ui.css"' in page
    assert 'src="https://cdn.jsdelivr.net/npm/swagger-ui-dist/swagger-ui-bundle.js"' in page
    assert "dom_id: '#swagger-ui'" in page
    assert 'url: "/doc"' in page


def test_the_rest_of_the_app_is_untouched() -> None:
    app = make_app()
    assert asyncio.run(app.request("/message")).json() == {"message": "hi"}
    assert asyncio.run(app.request("/doc")).json()["info"]["title"] == "t"
    assert asyncio.run(app.request("/ui", method="POST")).status == 404


def test_version_pins_the_assets() -> None:
    assert remote_assets("5.17.14") == {
        "css": ["https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"],
        "js": ["https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"],
    }
    assert "swagger-ui-dist@5.17.14/" in SwaggerUI({"url": "/doc", "version": "5.17.14"})


def test_options_render_as_data_or_javascript() -> None:
    rendered = render_options(
        {
            "url": "/doc",
            "deepLinking": True,
            "defaultModelsExpandDepth": -1,
            "validatorUrl": None,
            "spec": {"info": {"title": "</script><script>alert(1)</script>"}},
            "presets": ["SwaggerUIBundle.presets.apis", "SwaggerUIStandalonePreset"],
            "requestInterceptor": "(req) => req",
            "title": "not an option",
        }
    )
    lines = rendered.split(",\n            ")
    assert lines[:4] == ['url: "/doc"', "deepLinking: true", "defaultModelsExpandDepth: -1", "validatorUrl: null"]
    assert "</script>" not in lines[4]
    assert "\\u003c/script>" in lines[4]
    assert lines[5:] == [
        "presets: [SwaggerUIBundle.presets.apis,SwaggerUIStandalonePreset]",
        "requestInterceptor: (req) => req",
    ]


def test_manual_html() -> None:
    page = SwaggerUI({"url": "/doc", "manuallySwaggerUIHtml": lambda asset: f"<p>{asset['js'][0]}</p>"})
    assert page == "<p>https://cdn.jsdelivr.net/npm/swagger-ui-dist/swagger-ui-bundle.js</p>"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
