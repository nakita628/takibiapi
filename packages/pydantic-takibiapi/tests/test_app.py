from __future__ import annotations

import asyncio
import datetime as dt
import sys
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, computed_field

import takibiapi
from pydantic_takibiapi import Context, Model, ResponseValidationError, TakibiAPI, createRoute


class Todo(Model):
    id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=20, examples=["Buy milk"])
    completed: bool = False
    due: dt.date | None = None


class NewTodo(Model):
    title: str = Field(min_length=1, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=3)


class TodoIdParams(Model):
    todoId: int = Field(ge=1, description="The todo's id")


class ListQuery(Model):
    limit: int = Field(20, ge=1, le=100)
    status: Literal["open", "done"] | None = None
    tag: list[str] = Field(default_factory=list)


class AuthHeaders(Model):
    api_key: str = Field(alias="x-api-key", min_length=3)


class NotFound(Model):
    message: str


class getTodoRoute(
    createRoute(
        {
            "method": "get",
            "path": "/todos/{todoId}",
            "request": {"params": TodoIdParams},
            "responses": {
                200: {"description": "The todo", "content": {"application/json": {"schema": Todo}}},
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
            "responses": {200: {"description": "Todos", "content": {"application/json": {"schema": list[Todo]}}}},
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
                "body": {"content": {"application/json": {"schema": NewTodo}}, "required": True},
            },
            "responses": {201: {"description": "Created", "content": {"application/json": {"schema": Todo}}}},
        }
    )
):
    pass


class getBrokenRoute(
    createRoute(
        {
            "method": "get",
            "path": "/broken",
            "responses": {200: {"description": "", "content": {"application/json": {"schema": Todo}}}},
        }
    )
):
    pass


TODOS = {1: Todo(id=1, title="Buy milk")}


def getTodoRouteHandler(c: Context[getTodoRoute]):
    params = c.req.valid("param")
    todo = TODOS.get(params.todoId)
    if todo is None:
        return c.json(NotFound(message="no such todo"), 404)
    return c.json(todo)


def listTodosRouteHandler(c: Context[listTodosRoute]):
    q = c.req.valid("query")
    return c.json(
        [Todo(id=1, title=f"{q.status} {q.limit}"[:20]), *(Todo(id=i + 2, title=t) for i, t in enumerate(q.tag))]
    )


def postTodosRouteHandler(c: Context[postTodosRoute]):
    body = c.req.valid("json")
    headers = c.req.valid("header")
    assert isinstance(body, NewTodo)
    assert headers.api_key == "key"
    return c.json(Todo(id=2, title=body.title), 201)


def getBrokenRouteHandler(c: Context[getBrokenRoute]):
    return c.json({"id": 0, "title": "x"})  # type: ignore[arg-type]  # breaks ge=1


def make_app() -> TakibiAPI:
    app = TakibiAPI()
    app.openapi(getTodoRoute, getTodoRouteHandler)
    app.openapi(listTodosRoute, listTodosRouteHandler)
    app.openapi(postTodosRoute, postTodosRouteHandler)
    app.openapi(getBrokenRoute, getBrokenRouteHandler)
    app.doc("/doc", {"openapi": "3.1.0", "info": {"title": "Todo API", "version": "1.0.0"}})
    return app


def run(app: takibiapi.TakibiAPI, path: str, **kwargs: Any):
    return asyncio.run(app.request(path, **kwargs))


def errors(res: takibiapi.Response) -> list[tuple[str, str, str]]:
    assert res.status == 422, res.text()
    return [(e["in"], e["path"], e["code"]) for e in res.json()["errors"]]


HEADERS = {"x-api-key": "key"}


def test_path_params_with_constraints() -> None:
    app = make_app()
    assert run(app, "/todos/1").json() == {"id": 1, "title": "Buy milk", "completed": False, "due": None}
    assert run(app, "/todos/9").status == 404
    assert errors(run(app, "/todos/0")) == [("path", "todoId", "greater_than_equal")]  # ge=1 is checked
    assert errors(run(app, "/todos/x")) == [("path", "todoId", "int_parsing")]


def test_query() -> None:
    app = make_app()
    assert [t["title"] for t in run(app, "/todos?status=done&limit=5&tag=a&tag=b").json()] == ["done 5", "a", "b"]
    assert run(app, "/todos").json()[0]["title"] == "None 20"
    assert errors(run(app, "/todos?limit=0&status=nope")) == [
        ("query", "limit", "greater_than_equal"),
        ("query", "status", "literal_error"),
    ]


def test_json_body_is_strict() -> None:
    app = make_app()
    res = run(app, "/todos", method="POST", json={"title": "Walk"}, headers=HEADERS)
    assert (res.status, res.json()["title"]) == (201, "Walk")
    assert errors(run(app, "/todos", method="POST", json={"title": ""}, headers=HEADERS)) == [
        ("body", "title", "string_too_short")
    ]
    assert errors(run(app, "/todos", method="POST", json={"title": 1}, headers=HEADERS)) == [
        ("body", "title", "string_type")
    ]
    assert errors(run(app, "/todos", method="POST", json={"title": "x", "extra": 1}, headers=HEADERS)) == [
        ("body", "extra", "extra_forbidden")
    ]
    assert errors(run(app, "/todos", method="POST", json={"title": "x", "tags": ["a"] * 4}, headers=HEADERS)) == [
        ("body", "tags", "too_long")
    ]


def test_headers_use_aliases() -> None:
    app = make_app()
    assert errors(run(app, "/todos", method="POST", json={"title": "x"})) == [("header", "x-api-key", "missing")]
    assert errors(run(app, "/todos", method="POST", json={"title": "x"}, headers={"X-Api-Key": "k"})) == [
        ("header", "x-api-key", "string_too_short")
    ]


def test_malformed_and_unsupported_bodies() -> None:
    app = make_app()
    assert (
        run(app, "/todos", method="POST", body="{", headers={**HEADERS, "content-type": "application/json"}).status
        == 400
    )
    assert run(app, "/todos", method="POST", body="x", headers={**HEADERS, "content-type": "text/plain"}).status == 415


def test_responses_are_checked() -> None:
    app = make_app()
    assert run(app, "/broken").status == 500
    seen: list[BaseException] = []

    def on_error(c: Context[Any]):
        assert c.error is not None
        seen.append(c.error)
        return c.text("broken", 500)

    app.onError(on_error)
    run(app, "/broken")
    assert isinstance(seen[0], ResponseValidationError)
    assert seen[0].issues[0]["code"] == "greater_than_equal"


def test_document_31_and_30() -> None:
    doc = run(make_app(), "/doc").json()
    get = doc["paths"]["/todos/{todoId}"]["get"]
    assert get["parameters"] == [
        {
            "name": "todoId",
            "in": "path",
            "required": True,
            "schema": {"type": "integer", "minimum": 1, "description": "The todo's id"},
            "description": "The todo's id",
        }
    ]
    assert set(get["responses"]) == {"200", "404", "422"}
    schemas = doc["components"]["schemas"]
    assert {"Todo", "NewTodo", "NotFound", "ValidationProblem", "ValidationErrorItem"} <= set(schemas)
    assert schemas["Todo"]["properties"]["due"] == {
        "anyOf": [{"format": "date", "type": "string"}, {"type": "null"}],
        "default": None,
        "title": "Due",
    }
    assert schemas["Todo"]["additionalProperties"] is False
    headers = doc["paths"]["/todos"]["post"]["parameters"]
    assert headers == [
        {"name": "x-api-key", "in": "header", "required": True, "schema": {"type": "string", "minLength": 3}}
    ]
    query = {p["name"]: p for p in doc["paths"]["/todos"]["get"]["parameters"]}
    assert query["limit"]["required"] is False
    assert query["limit"]["schema"] == {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}

    doc30 = make_app().getOpenAPIDocument({"openapi": "3.0.0", "info": {"title": "t", "version": "1"}})
    todo30 = doc30["components"]["schemas"]["Todo"]
    assert todo30["properties"]["due"] == {
        "format": "date",
        "type": "string",
        "default": None,
        "title": "Due",
        "nullable": True,
    }
    assert todo30["properties"]["title"]["example"] == "Buy milk"


class Loose(BaseModel):  # pydantic's defaults: converts "1" to 1, ignores extra keys
    x: int


class StrictButOpen(BaseModel):
    model_config = ConfigDict(strict=True)
    x: int


class Wrapper(Model):
    inner: list[Loose]


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
        (StrictButOpen, "StrictButOpen is not strict"),
        (Wrapper, "Loose is not strict"),  # nested models too
        (dict[str, Annotated[Loose, "x"]], "Loose is not strict"),
    ],
)
def test_models_must_be_strict(schema: Any, message: str) -> None:
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
class Session(Model):
    session: str = Field(min_length=2)
    theme: Literal["light", "dark"] = "light"


class Problem(Model):
    title: str
    status: int


class Item(Model):  # reads and writes differently: label only exists in responses
    name: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        return self.name.upper()


class postItemRoute(
    createRoute(
        {
            "method": "post",
            "path": "/items",
            "request": {
                "cookies": Session,
                "body": {"content": {"application/json": {"schema": Item}}, "required": True},
            },
            "responses": {
                200: {"description": "The item", "content": {"application/json": {"schema": Item}}},
                409: {"description": "Conflict", "content": {"application/problem+json": {"schema": Problem}}},
            },
        }
    )
):
    pass


def postItemRouteHandler(c: Context[postItemRoute]):
    body = c.req.valid("json")
    cookies = c.req.valid("cookie")
    if body.name == "taken":
        return c.json(Problem(title="Conflict", status=409), 409, {"content-type": "application/problem+json"})
    if body.name == "bad-problem":
        return c.json({"title": 1}, 409, {"content-type": "application/problem+json"})  # pyright: ignore[reportArgumentType]
    if body.name == "undeclared":
        return c.json({"anything": True}, 299)  # pyright: ignore[reportArgumentType]  # no schema: not checked
    return c.json(Item(name=f"{body.name}:{cookies.theme}"))


def items_app() -> TakibiAPI:
    return TakibiAPI().openapi(postItemRoute, postItemRouteHandler)


COOKIE = {"cookie": "session=s1; theme=dark"}


def test_cookies_are_checked() -> None:
    app = items_app()
    assert run(app, "/items", method="POST", json={"name": "x"}, headers=COOKIE).json() == {
        "name": "x:dark",
        "label": "X:DARK",
    }
    assert errors(run(app, "/items", method="POST", json={"name": "x"}, headers={"cookie": "session=s"})) == [
        ("cookie", "session", "string_too_short")
    ]
    assert errors(run(app, "/items", method="POST", json={"name": "x"})) == [("cookie", "session", "missing")]


def test_responses_by_status_and_media_type() -> None:
    app = items_app()
    res = run(app, "/items", method="POST", json={"name": "taken"}, headers=COOKIE)
    assert (res.status, res.headers["content-type"], res.json()) == (
        409,
        "application/problem+json",
        {"title": "Conflict", "status": 409},
    )
    assert (
        run(app, "/items", method="POST", json={"name": "bad-problem"}, headers=COOKIE).status == 500
    )  # checked against Problem
    res = run(app, "/items", method="POST", json={"name": "undeclared"}, headers=COOKIE)
    assert (res.status, res.json()) == (299, {"anything": True})  # an undeclared status is written as it is


def test_input_and_output_schemas() -> None:
    doc = items_app().getOpenAPIDocument({"openapi": "3.1.0", "info": {"title": "t", "version": "1"}})
    op = doc["paths"]["/items"]["post"]
    assert op["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Item-Input"}
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Item-Output"
    }
    assert op["responses"]["409"]["content"]["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/Problem"
    }
    schemas = doc["components"]["schemas"]
    assert "label" not in schemas["Item-Input"]["properties"]
    assert schemas["Item-Output"]["properties"]["label"]["readOnly"] is True
    assert op["parameters"] == [
        {"name": "session", "in": "cookie", "required": True, "schema": {"type": "string", "minLength": 2}},
        {
            "name": "theme",
            "in": "cookie",
            "required": False,
            "schema": {"type": "string", "enum": ["light", "dark"], "default": "light"},
        },
    ]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
