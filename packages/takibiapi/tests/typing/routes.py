# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Static types of routes and handlers (checked by pyright; see typing_test.py)."""

from dataclasses import dataclass
from typing import TypedDict, reveal_type

from takibiapi import Context, TakibiAPI, createRoute


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
    c.json(Problem(detail="x"), 404)
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
