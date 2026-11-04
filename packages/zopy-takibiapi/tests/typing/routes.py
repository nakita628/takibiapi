# pyright: reportUnnecessaryTypeIgnoreComment=true
"""Static types of routes and handlers (checked by pyright; see typing_test.py)."""

from typing import reveal_type

from zopy_takibiapi import Context, OpenAPIObject, TakibiAPI, createRoute, z


class TodoParams(z.StrictObject):
    todoId = z.coerce.number().int().positive()


class NewTodo(z.StrictObject):
    title = z.string().min(1)


class Todo(OpenAPIObject, openapi="Todo"):
    id = z.int()
    title = z.string()


class Problem(z.StrictObject):
    detail = z.string()


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
    params = c.req.valid("param")
    body = c.req.valid("json")
    reveal_type(params, expected_text="TodoParams")
    reveal_type(params.todoId, expected_text="int")
    reveal_type(body.title, expected_text="str")
    c.json(Problem(detail="x"), 404)
    c.json(TodoParams(todoId=1))  # pyright: ignore[reportArgumentType]  # not a declared body
    return c.json(Todo(id=params.todoId, title=body.title))


class listTodosRoute(
    createRoute(
        {
            "method": "get",
            "path": "/todos",
            "responses": {200: {"description": "ok", "content": {"application/json": {"schema": z.array(Todo)}}}},
        }
    )
):
    pass


def listTodosRouteHandler(c: Context[listTodosRoute]):
    return c.json([Todo(id=1, title="x")])


app = TakibiAPI()
app.openapi(putTodoRoute, putTodoRouteHandler)
app.openapi(listTodosRoute, listTodosRouteHandler)
app.openapi(putTodoRoute, listTodosRouteHandler)  # pyright: ignore[reportArgumentType]  # another route's handler
