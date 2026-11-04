from __future__ import annotations

import asyncio
import datetime as dt
import sys
from typing import Any

import pytest

import takibiapi
from zopy_takibiapi import Context, ResponseValidationError, TakibiAPI, createRoute, z


def iso8601(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


IsoDateTime = z.codec(z.iso.datetime(), z.date(), decode=dt.datetime.fromisoformat, encode=iso8601)


class Todo(z.StrictObject):
    id = z.int().positive().openapi({"example": 1})
    title = z.string().min(1).max(20).openapi({"example": "Buy milk"})
    completed = z.boolean()
    createdAt = IsoDateTime


TodoSchema = Todo.openapi("Todo")


class NewTodo(z.StrictObject):
    title = z.string().min(1).max(20)
    tags = z.array(z.string()).max(3).optional()


NewTodoSchema = NewTodo.openapi("NewTodo")


class TodoIdParams(z.StrictObject):
    todoId = z.coerce.number().int().positive().openapi({"param": {"name": "todoId", "in": "path"}, "example": 1})


class ListQuery(z.StrictObject):
    limit = z.coerce.number().int().min(1).max(100).optional()
    status = z.enum(("open", "done")).optional()
    tag = z.array(z.string()).optional()


class AuthHeaders(z.StrictObject):
    authorization = z.string().startsWith("Bearer ")


class NotFound(z.StrictObject):  # not named: written in place
    message = z.string()


class getTodoRoute(
    createRoute(
        {
            "method": "get",
            "path": "/todos/{todoId}",
            "request": {"params": TodoIdParams},
            "responses": {
                200: {"description": "The todo", "content": {"application/json": {"schema": TodoSchema}}},
                404: {"description": "Not found", "content": {"application/json": {"schema": NotFound}}},
            },
        }
    )
):
    """Get one todo."""


class listTodosRoute(
    createRoute(
        {
            "method": "get",
            "path": "/todos",
            "request": {"query": ListQuery},
            "responses": {
                200: {"description": "Todos", "content": {"application/json": {"schema": z.array(TodoSchema)}}}
            },
        }
    )
):
    pass


class postTodosRoute(
    createRoute(
        {
            "method": "post",
            "path": "/todos",
            "request": {
                "headers": AuthHeaders,
                "body": {"content": {"application/json": {"schema": NewTodoSchema}}, "required": True},
            },
            "responses": {201: {"description": "Created", "content": {"application/json": {"schema": TodoSchema}}}},
        }
    )
):
    pass


class getBrokenRoute(
    createRoute(
        {
            "method": "get",
            "path": "/broken",
            "responses": {200: {"description": "", "content": {"application/json": {"schema": TodoSchema}}}},
        }
    )
):
    pass


CREATED = "2026-01-02T03:04:05Z"  # constructors parse, so a codec field takes its wire form
TODOS = {1: Todo(id=1, title="Buy milk", completed=False, createdAt=CREATED)}


def getTodoRouteHandler(c: Context[getTodoRoute]):
    params = c.req.valid("param")
    todo = TODOS.get(params.todoId)
    if todo is None:
        return c.json(NotFound(message="no such todo"), 404)
    return c.json(todo, 200)


def listTodosRouteHandler(c: Context[listTodosRoute]):
    query = c.req.valid("query")
    title = f"{query.status} {query.limit}"
    todos = [Todo(id=1, title=title, completed=False, createdAt=CREATED)]
    todos += [Todo(id=i + 2, title=t, completed=False, createdAt=CREATED) for i, t in enumerate(query.tag or [])]
    return c.json(todos, 200)


def postTodosRouteHandler(c: Context[postTodosRoute]):
    body = c.req.valid("json")
    assert isinstance(body, NewTodo)
    return c.json(Todo(id=2, title=body.title, completed=False, createdAt=CREATED), 201)


def getBrokenRouteHandler(c: Context[getBrokenRoute]):
    # A dict that breaks positive(): checked at run time (the type checker would reject it too).
    return c.json({"id": 0, "title": "x", "completed": False, "createdAt": CREATED}, 200)  # pyright: ignore[reportArgumentType]


def make_app() -> TakibiAPI:
    app = TakibiAPI()
    app.openapi(getTodoRoute, getTodoRouteHandler)
    app.openapi(listTodosRoute, listTodosRouteHandler)
    app.openapi(postTodosRoute, postTodosRouteHandler)
    app.openapi(getBrokenRoute, getBrokenRouteHandler)
    app.doc("/doc", {"openapi": "3.0.0", "info": {"title": "Todo API", "version": "1.0.0"}})
    return app


def run(app: takibiapi.TakibiAPI, path: str, **kwargs: Any):
    return asyncio.run(app.request(path, **kwargs))


def errors(res: takibiapi.Response) -> list[tuple[str, str, str]]:
    assert res.status == 422, res.text()
    return [(e["in"], e["path"], e["code"]) for e in res.json()["errors"]]


HEADERS = {"authorization": "Bearer secret"}


def test_path_params() -> None:
    app = make_app()
    res = run(app, "/todos/1")
    assert res.json() == {"id": 1, "title": "Buy milk", "completed": False, "createdAt": "2026-01-02T03:04:05Z"}
    assert run(app, "/todos/9").json() == {"message": "no such todo"}
    assert errors(run(app, "/todos/0")) == [("path", "todoId", "too_small")]
    assert errors(run(app, "/todos/x")) == [("path", "todoId", "invalid_type")]


def test_query() -> None:
    app = make_app()
    assert [t["title"] for t in run(app, "/todos?status=done&limit=5&tag=a&tag=b").json()] == ["done 5", "a", "b"]
    assert run(app, "/todos").json()[0]["title"] == "None None"
    assert errors(run(app, "/todos?limit=0&status=nope")) == [
        ("query", "limit", "too_small"),
        ("query", "status", "invalid_value"),
    ]
    # Only the declared keys are taken from the query string, so others (?utm_source=...) pass.
    assert run(app, "/todos?other=1").status == 200


def test_json_body() -> None:
    app = make_app()
    res = run(app, "/todos", method="POST", json={"title": "Walk"}, headers=HEADERS)
    assert (res.status, res.json()["title"]) == (201, "Walk")
    assert errors(run(app, "/todos", method="POST", json={"title": ""}, headers=HEADERS)) == [
        ("body", "title", "too_small")
    ]
    assert errors(run(app, "/todos", method="POST", json={"title": 1}, headers=HEADERS)) == [
        ("body", "title", "invalid_type")
    ]
    assert errors(run(app, "/todos", method="POST", json={"title": "x", "extra": 1}, headers=HEADERS)) == [
        ("body", "", "unrecognized_keys")
    ]
    assert errors(run(app, "/todos", method="POST", json={"title": "x", "tags": ["a"] * 4}, headers=HEADERS)) == [
        ("body", "tags", "too_big")
    ]


def test_headers() -> None:
    app = make_app()
    assert errors(run(app, "/todos", method="POST", json={"title": "x"})) == [
        ("header", "authorization", "invalid_type")
    ]
    assert errors(run(app, "/todos", method="POST", json={"title": "x"}, headers={"Authorization": "Basic x"})) == [
        ("header", "authorization", "invalid_format")
    ]


def test_malformed_and_unsupported_bodies() -> None:
    app = make_app()
    assert (
        run(app, "/todos", method="POST", body="{", headers={**HEADERS, "content-type": "application/json"}).status
        == 400
    )
    assert run(app, "/todos", method="POST", body="x", headers={**HEADERS, "content-type": "text/plain"}).status == 415


def test_responses_are_encoded_and_checked() -> None:
    app = make_app()
    assert run(app, "/broken").status == 500
    seen: list[Exception] = []

    def on_error(c: Context[Any]):
        assert c.error is not None
        seen.append(c.error)
        return c.text("broken", 500)

    app.onError(on_error)
    run(app, "/broken")
    assert isinstance(seen[0], ResponseValidationError)
    assert seen[0].issues[0]["code"] == "too_small"


def test_document() -> None:
    doc = run(make_app(), "/doc").json()
    get = doc["paths"]["/todos/{todoId}"]["get"]
    assert get["description"] == "Get one todo."
    assert get["parameters"] == [
        {
            "name": "todoId",
            "in": "path",
            "required": True,
            "schema": {"type": "integer", "minimum": 0, "exclusiveMinimum": True, "example": 1},
        }
    ]
    assert set(get["responses"]) == {"200", "404", "422"}
    # Only the .openapi("Name") schemas are components; NotFound is written in place.
    assert set(doc["components"]["schemas"]) == {"Todo", "NewTodo", "ValidationProblem", "ValidationErrorItem"}
    assert get["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Todo"}
    assert get["responses"]["404"]["content"]["application/json"]["schema"]["properties"] == {
        "message": {"type": "string"}
    }
    todo = doc["components"]["schemas"]["Todo"]
    assert todo["properties"]["createdAt"] == {"type": "string", "format": "date-time"}  # the codec's wire form
    assert todo["additionalProperties"] is False
    listing = doc["paths"]["/todos"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert listing == {"type": "array", "items": {"$ref": "#/components/schemas/Todo"}}
    doc31 = make_app().getOpenAPIDocument({"openapi": "3.1.0", "info": {"title": "t", "version": "1"}})
    assert doc31["components"]["schemas"]["Todo"]["properties"]["id"] == {
        "type": "integer",
        "exclusiveMinimum": 0,
        "example": 1,
    }


class Loose(z.Object):  # unknown keys are stripped: not strict
    x = z.int()


class HasLoose(z.StrictObject):
    inner = z.array(Loose)


def _route(schema: Any) -> Any:
    class postLooseRoute(
        createRoute(
            {
                "method": "post",
                "path": "/loose",
                "request": {"body": {"content": {"application/json": {"schema": schema}}}},
                "responses": {200: {"description": ""}},
            }
        )
    ):
        pass

    return postLooseRoute


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (Loose, "Loose is not strict"),
        (HasLoose, "Loose is not strict"),  # nested objects too
        (z.array(Loose).optional(), "Loose is not strict"),
        (int, "must be zopy schemas"),
    ],
)
def test_schemas_must_be_strict_zopy_schemas(schema: Any, message: str) -> None:
    route = _route(schema)

    def handler(c: Context[Any]):
        return c.text("x")

    handler.__annotations__["c"] = Context[route]
    with pytest.raises(TypeError, match=message):
        TakibiAPI().openapi(route, handler)


def test_cannot_mount_a_plain_takibi_app() -> None:
    with pytest.raises(TypeError, match="other schemas"):
        TakibiAPI().route("/v1", takibiapi.TakibiAPI())


# -- more of the public surface -------------------------------------------------------------- #
class Session(z.StrictObject):
    session = z.string().min(2).openapi({"description": "The session id"})
    theme = z.enum(("light", "dark")).default("light")


class Signup(z.StrictObject):
    password = z.string().min(8)
    confirm = z.string()
    startsAt = IsoDateTime


SignupSchema = Signup.refine(
    lambda form: form.password == form.confirm, {"error": "Passwords don't match", "path": ["confirm"]}
)


class Problem(z.StrictObject):
    title = z.string()
    status = z.int()


class Accepted(z.StrictObject):
    theme = z.string()
    startsAt = IsoDateTime


class postSignupRoute(
    createRoute(
        {
            "method": "post",
            "path": "/signup",
            "request": {
                "cookies": Session,
                "body": {"content": {"application/json": {"schema": SignupSchema}}, "required": True},
            },
            "responses": {
                200: {"description": "Accepted", "content": {"application/json": {"schema": Accepted}}},
                409: {"description": "Conflict", "content": {"application/problem+json": {"schema": Problem}}},
            },
        }
    )
):
    pass


def postSignupRouteHandler(c: Context[postSignupRoute]):
    body = c.req.valid("json")
    cookies = c.req.valid("cookie")
    assert isinstance(body.startsAt, dt.datetime)  # the codec decoded the wire string
    if body.password == "taken-password":
        return c.json(Problem(title="Conflict", status=409), 409, {"content-type": "application/problem+json"})
    if body.password == "bad-problem":
        return c.json({"title": 1}, 409, {"content-type": "application/problem+json"})  # pyright: ignore[reportArgumentType]
    if body.password == "undeclared":
        return c.json({"anything": True}, 299)  # pyright: ignore[reportArgumentType]  # no schema: not checked
    return c.json(Accepted(theme=cookies.theme, startsAt=iso8601(body.startsAt)), 200)


def signup_app() -> TakibiAPI:
    return TakibiAPI().openapi(postSignupRoute, postSignupRouteHandler)


COOKIE = {"cookie": "session=s1; theme=dark"}


def signup(password: str, confirm: str | None = None) -> dict[str, str]:
    return {"password": password, "confirm": confirm or password, "startsAt": "2026-01-02T03:04:05Z"}


def test_cookies_codecs_and_refinements() -> None:
    app = signup_app()
    res = run(app, "/signup", method="POST", json=signup("password1"), headers=COOKIE)
    assert (res.status, res.json()) == (200, {"theme": "dark", "startsAt": "2026-01-02T03:04:05Z"})
    assert errors(run(app, "/signup", method="POST", json=signup("password1", "different"), headers=COOKIE)) == [
        ("body", "confirm", "custom")
    ]
    assert errors(
        run(app, "/signup", method="POST", json={**signup("password1"), "startsAt": "yesterday"}, headers=COOKIE)
    ) == [("body", "startsAt", "invalid_format")]
    assert errors(run(app, "/signup", method="POST", json=signup("password1"), headers={"cookie": "session=s"})) == [
        ("cookie", "session", "too_small")
    ]


def test_responses_by_status_and_media_type() -> None:
    app = signup_app()
    res = run(app, "/signup", method="POST", json=signup("taken-password"), headers=COOKIE)
    assert (res.status, res.headers["content-type"], res.json()) == (
        409,
        "application/problem+json",
        {"title": "Conflict", "status": 409},
    )
    assert (
        run(app, "/signup", method="POST", json=signup("bad-problem"), headers=COOKIE).status == 500
    )  # checked against Problem
    res = run(app, "/signup", method="POST", json=signup("undeclared"), headers=COOKIE)
    assert (res.status, res.json()) == (299, {"anything": True})  # an undeclared status is written as it is


def test_refined_loose_objects_are_still_rejected() -> None:
    route = _route(Loose.refine(lambda v: True))

    def handler(c: Context[Any]):
        return c.text("x")

    handler.__annotations__["c"] = Context[route]
    with pytest.raises(TypeError, match="Loose is not strict"):
        TakibiAPI().openapi(route, handler)


def test_parameter_descriptions() -> None:
    doc = signup_app().getOpenAPIDocument({"openapi": "3.1.0", "info": {"title": "t", "version": "1"}})
    params = {p["name"]: p for p in doc["paths"]["/signup"]["post"]["parameters"]}
    assert params["session"]["description"] == "The session id"
    assert params["session"]["required"] is True
    assert params["theme"]["required"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
