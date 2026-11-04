# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Static types of routes and handlers (checked by pyright; see typing_test.py)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypedDict, reveal_type

from takibiapi import Context, Response, ResponseConfig, TakibiAPI, TypedResponse, createRoute


class TodoParams(TypedDict):
    todoId: int


class Search(TypedDict):
    q: str


@dataclass
class NewTodo:
    title: str


@dataclass
class Todo:
    id: int
    title: str


@dataclass
class Problem:
    detail: str


@dataclass
class Other:
    x: int


class putTodoRoute(
    createRoute(
        {
            "method": "put",
            "path": "/todos/{todoId}",
            "request": {
                "params": TodoParams,
                "query": Search,
                "body": {"content": {"application/json": {"schema": NewTodo}}},
            },
            "responses": {
                200: {"description": "ok", "content": {"application/json": {"schema": Todo}}},
                404: {"description": "missing", "content": {"application/problem+json": {"schema": Problem}}},
            },
        }
    )
):
    """Update a todo."""


async def putTodoRouteHandler(c: Context[putTodoRoute]):
    body = c.req.valid("json")
    reveal_type(body, expected_text="NewTodo")
    reveal_type(c.req.valid("param")["todoId"], expected_text="int")
    reveal_type(c.req.valid("query"), expected_text="Search")
    c.req.valid("header")  # no headers type: Any
    reveal_type(await c.req.json(), expected_text="Any")  # the raw, unchecked body
    reveal_type(c.req.param("todoId"), expected_text="str")  # the raw text
    reveal_type(c.req.query("q"), expected_text="str | None")
    reveal_type(c.req.queries("tag"), expected_text="list[str]")
    c.json(Other(x=1))  # pyright: ignore[reportArgumentType]  # not a declared response body
    c.json({"id": 1, "title": "x"})  # pyright: ignore[reportArgumentType]  # a dict is not a Todo
    reveal_type(c.json(Problem(detail="x"), 404), expected_text="TypedResponse")
    c.json(Problem(detail="x"), 500)  # pyright: ignore[reportArgumentType]  # 500 is not declared
    c.text("x", 500)  # pyright: ignore[reportArgumentType]  # nor for text
    c.body(status=204)  # pyright: ignore[reportArgumentType]  # nor 204 without content
    params = c.req.valid("param")
    return c.json(Todo(id=params["todoId"], title=body.title), 200)


class getOtherRoute(
    createRoute(
        {
            "method": "get",
            "path": "/other",
            "responses": {200: {"description": "ok", "content": {"application/json": {"schema": list[Search]}}}},
        }
    )
):
    pass


def getOtherRouteHandler(c: Context[getOtherRoute]):
    c.json([{"q": "x"}])
    c.json([{"name": "x"}])  # pyright: ignore[reportArgumentType]  # wrong TypedDict key
    return c.json([])


app = TakibiAPI()
app.openapi(putTodoRoute, putTodoRouteHandler)
app.openapi(getOtherRoute, getOtherRouteHandler)
app.openapi(putTodoRoute, getOtherRouteHandler)  # pyright: ignore[reportArgumentType]  # another route's handler


# -- statuses ---------------------------------------------------------------------------------- #

errors: Mapping[Literal[400, 404], ResponseConfig[Problem]] = {
    400: {"description": "bad", "content": {"application/json": {"schema": Problem}}},
    404: {"description": "missing", "content": {"application/json": {"schema": Problem}}},
}


class createTodoRoute(
    createRoute(
        {
            "method": "post",
            "path": "/todos",
            "responses": {
                **errors,
                201: {"description": "created", "content": {"application/json": {"schema": Todo}}},
                204: {"description": "nothing to create"},
            },
        }
    )
):
    pass


def createTodoRouteHandler(c: Context[createTodoRoute]):
    if c.req.query("dry"):
        return c.body(status=204)
    if c.req.query("bad"):
        return c.json(Problem(detail="bad"), 400)
    c.json(Todo(id=1, title="x"))  # pyright: ignore[reportArgumentType]  # 200 (the default) is not declared
    return c.json(Todo(id=1, title="x"), 201)


reveal_type(createTodoRouteHandler, expected_text="(c: Context[createTodoRoute]) -> TypedResponse")


class withDefaultRoute(
    createRoute(
        {
            "method": "get",
            "path": "/default",
            "responses": {
                200: {"description": "ok", "content": {"application/json": {"schema": Todo}}},
                "default": {"description": "error", "content": {"application/json": {"schema": Problem}}},
            },
        }
    )
):
    pass


def withDefaultRouteHandler(c: Context[withDefaultRoute]):
    if c.req.query("x"):
        return c.text("teapot", 418)  # "default" covers any status
    return c.json(Problem(detail="x"), 503)


# Keys typed `int` are not statuses (pyright reports it as either rule, depending on what it checks first).
untyped: dict[int, ResponseConfig[Problem]] = {}
createRoute({"method": "get", "path": "/u", "responses": {**untyped}})  # pyright: ignore[reportCallIssue, reportArgumentType]


def returnsResponse(c: Context[getOtherRoute]) -> Response:
    return c.json([])


def returnsTyped(c: Context[getOtherRoute]) -> TypedResponse:
    return c.json([])


app.openapi(createTodoRoute, createTodoRouteHandler)
app.openapi(withDefaultRoute, withDefaultRouteHandler)
app.openapi(getOtherRoute, returnsTyped)
app.openapi(getOtherRoute, returnsResponse)  # pyright: ignore[reportArgumentType]  # a plain Response is not a declared one
