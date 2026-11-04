"""``takibiapi.cors``: the headers Hono's ``hono/cors`` sends, for preflights and other requests."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

import pytest

from takibiapi import Context, TakibiAPI, createRoute
from takibiapi.cors import CORSOptions, cors


@dataclass
class Message:
    message: str


class getMessageRoute(
    createRoute(
        {
            "method": "get",
            "path": "/api/message",
            "responses": {200: {"description": "A message", "content": {"application/json": {"schema": Message}}}},
        }
    )
):
    """Get a message."""


def getMessageRouteHandler(c: Context[getMessageRoute]):
    return c.json(Message("hi"))


def make_app(options: CORSOptions | None = None, prefix: str = "") -> TakibiAPI:
    app = TakibiAPI().openapi(getMessageRoute, getMessageRouteHandler)
    app.use(cors(options), prefix=prefix)
    return app


def run(app: TakibiAPI, path: str, **kwargs: Any):
    return asyncio.run(app.request(path, **kwargs))


def test_defaults_allow_any_origin() -> None:
    res = run(make_app(), "/api/message", headers={"Origin": "https://a.example"})
    assert res.status == 200
    assert res.headers["access-control-allow-origin"] == "*"
    assert "vary" not in res.headers


def test_preflight_defaults() -> None:
    res = run(
        make_app(),
        "/api/message",
        method="OPTIONS",
        headers={"Origin": "https://a.example", "Access-Control-Request-Headers": "X-Custom, Content-Type"},
    )
    assert res.status == 204
    assert res.body == b""
    assert res.headers["access-control-allow-origin"] == "*"
    assert res.headers["access-control-allow-methods"] == "GET,HEAD,PUT,POST,DELETE,PATCH"
    assert res.headers["access-control-allow-headers"] == "X-Custom,Content-Type"
    assert res.headers["vary"] == "Access-Control-Request-Headers"


def test_single_origin_with_options() -> None:
    app = make_app(
        {
            "origin": "https://a.example",
            "allowHeaders": ["X-Custom"],
            "allowMethods": ["GET", "POST"],
            "exposeHeaders": ["X-Total", "X-Next"],
            "maxAge": 600,
            "credentials": True,
        }
    )
    res = run(app, "/api/message", headers={"Origin": "https://a.example"})
    assert res.headers["access-control-allow-origin"] == "https://a.example"
    assert res.headers["access-control-allow-credentials"] == "true"
    assert res.headers["access-control-expose-headers"] == "X-Total,X-Next"
    assert res.headers["vary"] == "Origin"

    pre = run(app, "/api/message", method="OPTIONS", headers={"Origin": "https://a.example"})
    assert pre.status == 204
    assert pre.headers["access-control-max-age"] == "600"
    assert pre.headers["access-control-allow-methods"] == "GET,POST"
    assert pre.headers["access-control-allow-headers"] == "X-Custom"
    assert pre.headers["vary"] == "Origin, Access-Control-Request-Headers"

    other = run(app, "/api/message", headers={"Origin": "https://b.example"})
    assert "access-control-allow-origin" not in other.headers


def test_origin_list_and_function() -> None:
    listed = make_app({"origin": ["https://a.example", "https://b.example"]})
    assert (
        run(listed, "/api/message", headers={"Origin": "https://b.example"}).headers["access-control-allow-origin"]
        == "https://b.example"
    )
    assert (
        "access-control-allow-origin"
        not in run(listed, "/api/message", headers={"Origin": "https://c.example"}).headers
    )

    async def by_suffix(origin: str, c: Context[Any]) -> str | None:
        return origin if origin.endswith(".example.com") else "https://example.com"

    fn = make_app({"origin": by_suffix, "allowMethods": lambda origin, c: ["GET"]})
    assert (
        run(fn, "/api/message", headers={"Origin": "https://x.example.com"}).headers["access-control-allow-origin"]
        == "https://x.example.com"
    )
    pre = run(fn, "/api/message", method="OPTIONS", headers={"Origin": "https://evil.test"})
    assert pre.headers["access-control-allow-origin"] == "https://example.com"
    assert pre.headers["access-control-allow-methods"] == "GET"


@pytest.mark.parametrize(("path", "expected"), [("/api/message", "*"), ("/doc", None)])
def test_prefix(path: str, expected: str | None) -> None:
    app = make_app(prefix="/api")
    app.doc("/doc", {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}})
    assert run(app, path).headers.get("access-control-allow-origin") == expected


def test_error_responses_get_the_headers() -> None:
    res = run(make_app(), "/nowhere")
    assert res.status == 404
    assert res.headers["access-control-allow-origin"] == "*"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
