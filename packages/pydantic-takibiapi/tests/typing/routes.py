# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Static types of routes and handlers (checked by pyright; see typing_test.py)."""

from typing import reveal_type

from pydantic import Field

from pydantic_takibiapi import Context, Model, TakibiAPI, createRoute


class TodoParams(Model):
    todoId: int = Field(ge=1)


class NewTodo(Model):
    title: str = Field(min_length=1)


class Todo(Model):
    id: int
    title: str


class Problem(Model):
    detail: str


class putTodoRoute(
    createRoute(
        {
            "method": "put",
            "path": "/todos/{todoId}",
            "request": {"params": TodoParams, "body": {"content": {"application/json": {"schema": NewTodo}}}},
            "responses": {
                200: {"description": "ok", "content": {"application/json": {"schema": Todo}}},
                404: {"description": "missing", "content": {"application/json": {"schema": Problem}}},
            },
        }
    )
):
    pass


def putTodoRouteHandler(c: Context[putTodoRoute]):
    reveal_type(c.req.valid("param"), expected_text="TodoParams")
    reveal_type(c.req.valid("param").todoId, expected_text="int")
    reveal_type(c.req.valid("json").title, expected_text="str")
    c.json(Problem(detail="x"), 404)
    c.json({"id": 1, "title": "x"})  # pyright: ignore[reportArgumentType]  # a dict is not a Todo
    c.json(TodoParams(todoId=1))  # pyright: ignore[reportArgumentType]  # not a declared body
    params = c.req.valid("param")
    body = c.req.valid("json")
    return c.json(Todo(id=params.todoId, title=body.title))


class getOtherRoute(createRoute({"method": "get", "path": "/other", "responses": {200: {"description": "ok"}}})):
    pass


def getOtherRouteHandler(c: Context[getOtherRoute]):
    return c.text("x")


app = TakibiAPI()
app.openapi(putTodoRoute, putTodoRouteHandler)
app.openapi(putTodoRoute, getOtherRouteHandler)  # pyright: ignore[reportArgumentType]  # another route's handler
