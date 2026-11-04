"""The Rust router (takibiapi._core.Router) matches exactly as the Python one (takibiapi.router.Router)."""

from __future__ import annotations

import random
import sys
from typing import Any

import pytest

from takibiapi import schema
from takibiapi.router import Router

ROUTES = [
    ("GET", "/"),
    ("GET", "/todos"),
    ("POST", "/todos"),
    ("GET", "/todos/{todoId}"),
    ("DELETE", "/todos/{todoId}"),
    ("GET", "/todos/{todoId}/tags"),
    ("GET", "/todos/{todoId}/tags/{tag}"),
    ("GET", "/todos/search"),  # static wins over {todoId}
    ("GET", "/users/{userId}/todos/{todoId}"),
    ("PUT", "/users/me/todos/{todoId}"),
]

PATHS = [
    "/",
    "/todos",
    "/todos/",
    "//todos",
    "/todos/1",
    "/todos/search",
    "/todos/1/tags",
    "/todos/1/tags/x",
    "/todos/1/tags/x/y",
    "/users/me/todos/3",
    "/users/7/todos/3",
    "/nope",
    "/todos/%20",
    "/todos/é",
]


def routers() -> list[Any]:
    out: list[Any] = [Router()]
    if schema._core is not None:
        out.append(schema._core.Router())
    for router in out:
        for method, path in ROUTES:
            router.add(method, path, f"{method} {path}")
    return out


@pytest.mark.skipif(schema._core is None, reason="the Rust engine (takibiapi._core) is not built")
def test_same_matches() -> None:
    python, rust = routers()
    rng = random.Random(0)
    segments = ["todos", "tags", "users", "me", "search", "1", "x", ""]
    paths = PATHS + ["/" + "/".join(rng.choice(segments) for _ in range(rng.randrange(6))) for _ in range(500)]
    for path in paths:
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            assert rust.match(method, path) == python.match(method, path), (method, path)
        assert rust.allowed(path) == python.allowed(path), path


def test_matches() -> None:
    for router in routers():
        assert router.match("GET", "/todos/1") == ("GET /todos/{todoId}", {"todoId": "1"})
        assert router.match("GET", "/todos/search") == ("GET /todos/search", {})
        assert router.match("PUT", "/users/me/todos/3") == ("PUT /users/me/todos/{todoId}", {"todoId": "3"})
        assert router.match("GET", "/users/me/todos/3") == (
            "GET /users/{userId}/todos/{todoId}",
            {"userId": "me", "todoId": "3"},
        )
        assert router.match("PATCH", "/todos/1") is None
        assert router.allowed("/todos/1") == ["DELETE", "GET"]


def test_errors() -> None:
    for router in routers():
        with pytest.raises(ValueError, match="already registered"):
            router.add("GET", "/todos/{todoId}", "again")
        with pytest.raises(ValueError, match="already registered"):
            router.add("GET", "/todos/", "again")
        with pytest.raises(ValueError, match="conflicting parameter names"):
            router.add("GET", "/todos/{id}/other", "x")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
