from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Literal, NotRequired, TypedDict

import pytest

from takibiapi import Context, Response, TakibiAPI, createRoute, schema
from takibiapi.app import FallbackNext, Next


@dataclass
class Todo:
    id: int
    title: Annotated[str, {"minLength": 1, "example": "Buy milk"}]
    completed: bool = False


@dataclass
class NewTodo:
    title: Annotated[str, {"minLength": 1}]  # documentation: not checked
    tags: list[str] | None = None


class TodoIdParams(TypedDict):
    todoId: Annotated[int, {"minimum": 1, "description": "The todo's id"}]


class ListQuery(TypedDict):
    limit: NotRequired[int]
    status: NotRequired[Literal["open", "done"]]
    tag: NotRequired[list[str]]


AuthHeaders = TypedDict("AuthHeaders", {"x-api-key": str})


class NotFound(TypedDict):
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


class deleteTodoRoute(
    createRoute(
        {
            "method": "delete",
            "path": "/todos/{todoId}",
            "request": {"params": TodoIdParams},
            "responses": {204: {"description": "Deleted"}},
        }
    )
):
    pass


class getFailRoute(
    createRoute({"method": "get", "path": "/fail", "responses": {200: {"description": ""}}, "hide": True})
):
    pass


TODOS = {1: Todo(1, "Buy milk")}


# Every test runs on both engines: Rust (takibiapi._core) and the pure-Python reference.
@pytest.fixture(autouse=True, params=["rust", "python"])
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "rust" and schema._core is None:
        pytest.skip("the Rust engine (takibiapi._core) is not built")
    if request.param == "python":
        monkeypatch.setattr(schema, "_core", None)
    return request.param


def getTodoRouteHandler(c: Context[getTodoRoute]):
    params = c.req.valid("param")  # todoId: an int, checked and converted from the path text
    assert isinstance(params["todoId"], int)
    todo = TODOS.get(params["todoId"])
    if todo is None:
        return c.json({"message": f"no todo {params['todoId']}"}, 404)
    return c.json(todo)


async def listTodosRouteHandler(c: Context[listTodosRoute]):
    q = c.req.valid("query")
    return c.json([Todo(1, f"{q.get('status')} {q.get('tag')} {q.get('limit')!r}")])


async def postTodosRouteHandler(c: Context[postTodosRoute]):
    body = c.req.valid("json")  # a NewTodo instance
    assert isinstance(body, NewTodo)
    c.header("location", "/todos/2")
    return c.json(Todo(2, body.title), 201)


def deleteTodoRouteHandler(c: Context[deleteTodoRoute]):
    return c.body(status=204)


def getFailRouteHandler(c: Context[getFailRoute]):
    raise RuntimeError("boom")


def make_app() -> TakibiAPI:
    app = TakibiAPI()
    app.openapi(getTodoRoute, getTodoRouteHandler)
    app.openapi(listTodosRoute, listTodosRouteHandler)
    app.openapi(postTodosRoute, postTodosRouteHandler)
    app.openapi(deleteTodoRoute, deleteTodoRouteHandler)
    app.openapi(getFailRoute, getFailRouteHandler)
    app.doc("/doc", {"openapi": "3.0.0", "info": {"title": "Todo API", "version": "1.0.0"}})
    return app


def run(app: TakibiAPI, path: str, **kwargs: Any):
    return asyncio.run(app.request(path, **kwargs))


HEADERS = {"x-api-key": "k"}


def test_path_params_are_checked_and_converted() -> None:
    app = make_app()
    res = run(app, "/todos/1")
    assert (res.status, res.json()) == (200, {"id": 1, "title": "Buy milk", "completed": False})
    assert res.headers["content-type"] == "application/json"
    assert run(app, "/todos/9").status == 404
    bad = run(app, "/todos/abc")
    assert (bad.status, bad.headers["content-type"]) == (422, "application/problem+json")
    assert bad.json() == {
        "type": "/problems/validation-error",
        "title": "Unprocessable Content",
        "status": 422,
        "detail": "Invalid path parameters.",
        "instance": "/todos/abc",
        "errors": [
            {"in": "path", "path": "todoId", "code": "invalid_type", "message": "Expected integer, received str"}
        ],
    }


def test_constraints_are_not_checked() -> None:
    app = make_app()
    assert run(app, "/todos/0").status == 404  # minimum: 1 is documentation: 0 is an int
    assert run(app, "/todos", method="POST", json={"title": ""}, headers=HEADERS).status == 201  # minLength too


def test_query() -> None:
    app = make_app()
    res = run(app, "/todos?status=done&tag=a&tag=b&limit=5&other=x")
    assert res.json()[0]["title"] == "done ['a', 'b'] 5"
    assert run(app, "/todos").json()[0]["title"] == "None None None"
    bad = run(app, "/todos?status=nope&limit=x")
    assert [(e["path"], e["code"]) for e in bad.json()["errors"]] == [
        ("limit", "invalid_type"),
        ("status", "invalid_value"),
    ]


def test_json_body() -> None:
    app = make_app()
    res = run(app, "/todos", method="POST", json={"title": "Walk", "extra": 1}, headers=HEADERS)
    assert (res.status, res.json(), res.headers["location"]) == (
        201,
        {"id": 2, "title": "Walk", "completed": False},
        "/todos/2",
    )
    bad = run(app, "/todos", method="POST", json={"title": 1, "tags": ["a", 2]}, headers=HEADERS)
    assert bad.status == 422
    assert [(e["in"], e["path"], e["code"]) for e in bad.json()["errors"]] == [
        ("body", "title", "invalid_type"),
        ("body", "tags.1", "invalid_type"),
    ]
    assert run(app, "/todos", method="POST", json={}, headers=HEADERS).json()["errors"][0]["code"] == "missing"


def test_headers_are_checked() -> None:
    res = run(make_app(), "/todos", method="POST", json={"title": "Walk"})
    assert res.status == 422
    assert res.json()["errors"] == [{"in": "header", "path": "x-api-key", "code": "missing", "message": "Required"}]


def test_malformed_json_and_wrong_content_type() -> None:
    app = make_app()
    res = run(app, "/todos", method="POST", body="{", headers={**HEADERS, "content-type": "application/json"})
    assert (res.status, res.headers["content-type"]) == (400, "application/problem+json")
    assert run(app, "/todos", method="POST", body="x", headers={**HEADERS, "content-type": "text/plain"}).status == 415


def test_no_content() -> None:
    res = run(make_app(), "/todos/1", method="DELETE")
    assert (res.status, res.body) == (204, b"")


def test_not_found_method_not_allowed_and_errors() -> None:
    app = make_app()
    missing = run(app, "/nope")
    assert (missing.status, missing.json()["title"]) == (404, "Not Found")
    wrong = run(app, "/todos/1", method="PATCH")
    assert (wrong.status, wrong.headers["allow"]) == (405, "DELETE, GET")
    assert run(app, "/fail").status == 500
    app.onError(lambda c: c.text(f"failed: {c.error}", 503))
    assert (run(app, "/fail").status, run(app, "/fail").text()) == (503, "failed: boom")
    app.notFound(lambda c: c.text("custom", 404))
    assert run(app, "/nope").text() == "custom"
    assert run(app, "/todos/1", method="PATCH").status == 405  # a known path still answers 405


def test_error_and_not_found_handlers_compose() -> None:
    """As Hono v5: middleware ``(c, next)`` runs around the later registrations, a handler ``(c)``
    answers, ``prefix`` scopes, and a chain that ends without a response leaves the built-in one."""
    app = make_app()
    seen: list[str] = []

    async def tag(c: Context[Any], next: FallbackNext) -> Response | None:
        res = await next()
        seen.append("none" if res is None else str(res.status))
        if res is not None:
            res.headers["x-handled"] = "yes"
        return res

    app.onError(tag)
    res = run(app, "/fail")
    assert (res.status, "x-handled" in res.headers, seen) == (500, False, ["none"])  # nothing after tag
    app.onError(lambda c: c.text("api failed", 503), prefix="/api")  # not for /fail
    assert run(app, "/fail").status == 500
    app.onError(lambda c: c.text(f"failed: {c.error}", 503))
    res = run(app, "/fail")
    assert (res.status, res.text(), res.headers["x-handled"]) == (503, "failed: boom", "yes")
    app.notFound(tag, lambda c: c.text("not here", 404))
    res = run(app, "/nope")
    assert (res.status, res.text(), res.headers["x-handled"]) == (404, "not here", "yes")
    app.notFound(lambda c: c.text("no such API", 404), prefix="/api")  # later, but a prefix is no priority
    assert run(app, "/api/nope").text() == "not here"
    app.notFound(lambda c: None)  # answers nothing: the next handler is asked
    assert run(app, "/nope").text() == "not here"


def test_mounted_apps_handle_their_own_errors() -> None:
    v1 = TakibiAPI().openapi(getFailRoute, getFailRouteHandler)
    v1.onError(lambda c: c.text(f"v1: {c.error}", 503))
    v1.notFound(lambda c: c.text("v1: not here", 404))
    v2 = TakibiAPI().openapi(getFailRoute, getFailRouteHandler)  # no handler of its own
    app = TakibiAPI().route("/v1", v1).route("/v2", v2)
    app.onError(lambda c: c.text(f"root: {c.error}", 500))
    app.notFound(lambda c: c.text("root: not here", 404))
    assert (run(app, "/v1/fail").status, run(app, "/v1/fail").text()) == (503, "v1: boom")  # the sub-app's comes first
    assert run(app, "/v2/fail").text() == "root: boom"  # else the parent's
    assert run(app, "/v1/nope").text() == "v1: not here"  # notFound too, for its paths
    assert run(app, "/nope").text() == "root: not here"
    outer = TakibiAPI().route("/api", TakibiAPI().route("/v1", v1))
    assert run(outer, "/api/v1/fail").text() == "v1: boom"  # through nested mounts too


def test_a_failing_error_handler_still_answers() -> None:
    app = make_app()

    def broken(c: Context[Any]):
        raise RuntimeError("the handler broke")

    app.onError(broken)
    res = run(app, "/fail")
    assert (res.status, res.json()["title"]) == (500, "Internal Server Error")


def test_head_falls_back_to_get() -> None:
    assert run(make_app(), "/todos/1", method="HEAD").status == 200


def test_middleware() -> None:
    app = make_app()
    calls: list[str] = []

    async def cors(c: Context[Any], next: Next):
        calls.append(c.req.path)
        res = await next()
        res.headers["access-control-allow-origin"] = "*"
        return res

    async def auth(c: Context[Any], next: Next):
        if c.req.header("x-api-key") is None:
            return c.text("unauthorized", 401)
        c.set("user", "alice")
        return await next()

    app.use(cors)
    app.use(auth, prefix="/todos")
    assert run(app, "/todos/1").status == 401
    res = run(app, "/todos/1", headers={"X-Api-Key": "k"})
    assert (res.status, res.headers["access-control-allow-origin"]) == (200, "*")
    assert run(app, "/doc").status == 200  # outside /todos: no auth
    assert calls == ["/todos/1", "/todos/1", "/doc"]


def test_mounted_sub_app() -> None:
    api = TakibiAPI().openapi(getTodoRoute, getTodoRouteHandler)
    app = TakibiAPI().route("/v1", api)
    assert run(app, "/v1/todos/1").status == 200
    assert (
        "/v1/todos/{todoId}"
        in app.getOpenAPIDocument({"openapi": "3.1.0", "info": {"title": "t", "version": "1"}})["paths"]
    )


def test_doc_endpoint() -> None:
    doc = run(make_app(), "/doc").json()
    assert doc["openapi"] == "3.0.0"
    assert doc["info"] == {"title": "Todo API", "version": "1.0.0"}
    assert "/fail" not in doc["paths"]  # hide: True
    get = doc["paths"]["/todos/{todoId}"]["get"]
    assert get["description"] == "Get one todo."
    assert get["parameters"] == [
        {
            "name": "todoId",
            "in": "path",
            "required": True,
            "schema": {"type": "integer", "minimum": 1, "description": "The todo's id"},
            "description": "The todo's id",
        }
    ]
    assert set(get["responses"]) == {"200", "404", "422"}  # 422: what takibi answers for wrong types
    post = doc["paths"]["/todos"]["post"]
    assert post["parameters"] == [{"name": "x-api-key", "in": "header", "required": True, "schema": {"type": "string"}}]
    assert post["requestBody"] == {
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewTodo"}}},
        "required": True,
    }
    assert set(doc["components"]["schemas"]) == {
        "Todo",
        "NewTodo",
        "NotFound",
        "ValidationProblem",
        "ValidationErrorItem",
    }


def test_openapi_version_is_required() -> None:
    app = make_app()
    with pytest.raises(ValueError, match="openapi"):
        app.doc("/spec", {"info": {"title": "t", "version": "1"}})
    with pytest.raises(ValueError, match="openapi"):
        app.getOpenAPIDocument({"openapi": "2.0", "info": {"title": "t", "version": "1"}})
    with pytest.raises(ValueError, match="info"):
        app.doc("/spec", {"openapi": "3.1.0"})


def test_route_must_be_a_class() -> None:
    value_route = createRoute({"method": "get", "path": "/x", "responses": {200: {"description": ""}}})

    def handler(c: Context[Any]):
        return c.text("x")

    with pytest.raises(TypeError, match="declare the route as a class"):
        TakibiAPI().openapi(value_route, handler)


def test_handler_must_take_its_routes_context() -> None:
    app = TakibiAPI()

    def bare(c: Context):  # type: ignore[type-arg]
        return c.text("x")

    def unannotated(c):  # type: ignore[no-untyped-def]
        return c.text("x")

    def no_args():
        return Response(b"x")

    for handler in (bare, unannotated, no_args, lambda c: c.text("x"), listTodosRouteHandler):
        with pytest.raises(TypeError, match=r"getTodoRouteHandler\(c: Context\[getTodoRoute\]\)|takes no context"):
            app.openapi(getTodoRoute, handler)  # type: ignore[arg-type]


def test_handler_must_return_a_response() -> None:
    app = TakibiAPI()

    def getFailRouteHandler(c: Context[getFailRoute]):
        return {"not": "a response"}

    app.openapi(getFailRoute, getFailRouteHandler)  # pyright: ignore[reportArgumentType]  # the type checker rejects it too
    assert run(app, "/fail").status == 500


def test_asgi() -> None:
    app = make_app()
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/todos/1", "query_string": b"", "headers": []}
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 200
    assert (b"content-type", b"application/json") in sent[0]["headers"]
    assert sent[1]["body"] == b'{"id":1,"title":"Buy milk","completed":false}'


def test_rsgi() -> None:
    class Scope:
        proto = "http"
        method = "POST"
        path = "/todos"
        query_string = ""
        headers: ClassVar = {"Content-Type": "application/json", "x-api-key": "k"}  # RSGI: str pairs

    class Protocol:
        sent: tuple[int, list[tuple[str, str]], bytes] | None = None

        async def __call__(self) -> bytes:
            return b'{"title": "Walk"}'

        def response_bytes(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
            self.sent = (status, headers, body)

    protocol = Protocol()
    asyncio.run(make_app().__rsgi__(Scope(), protocol))
    assert protocol.sent is not None
    status, headers, body = protocol.sent
    assert (status, body) == (201, b'{"id":2,"title":"Walk","completed":false}')
    assert ("location", "/todos/2") in headers


# -- more of the public surface -------------------------------------------------------------- #
@dataclass
class Session:
    session: str
    theme: Literal["light", "dark"] = "light"


@dataclass
class Echo:
    cookie: str
    theme: str
    raw_tags: list[str]
    text: str


class postEchoRoute(
    createRoute(
        {
            "method": "post",
            "path": "/echo",
            "summary": "Echo the request",
            "tags": ["debug"],
            "operationId": "postEcho",
            "deprecated": True,
            "security": [{"Bearer": []}],
            "request": {"cookies": Session},
            "responses": {200: {"description": "The echo", "content": {"application/json": {"schema": Echo}}}},
        }
    )
):
    pass


async def postEchoRouteHandler(c: Context[postEchoRoute]):
    cookies = c.req.valid("cookie")  # Session: checked, theme defaulted
    assert isinstance(cookies, Session)
    return c.json(Echo(c.req.cookie("session") or "", cookies.theme, c.req.queries("tag"), await c.req.text()))


def test_cookies_are_checked() -> None:
    app = TakibiAPI().openapi(postEchoRoute, postEchoRouteHandler)
    res = run(app, "/echo?tag=a&tag=b", method="POST", body="hi", headers={"cookie": "session=s1; theme=dark"})
    assert res.json() == {"cookie": "s1", "theme": "dark", "raw_tags": ["a", "b"], "text": "hi"}
    assert run(app, "/echo", method="POST", headers={"cookie": "session=s1"}).json()["theme"] == "light"
    bad = run(app, "/echo", method="POST", headers={"cookie": "theme=blue"})
    assert bad.status == 422
    assert [(e["in"], e["path"], e["code"]) for e in bad.json()["errors"]] == [
        ("cookie", "session", "missing"),
        ("cookie", "theme", "invalid_value"),
    ]


def test_operation_metadata_and_config_in_the_document() -> None:
    app = TakibiAPI().openapi(postEchoRoute, postEchoRouteHandler)
    config: Any = {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "tags": [{"name": "debug"}],
        "security": [{"Bearer": []}],
        "components": {"securitySchemes": {"Bearer": {"type": "http", "scheme": "bearer"}}},
    }
    doc = app.getOpenAPIDocument(config)
    op = doc["paths"]["/echo"]["post"]
    assert {k: op[k] for k in ("summary", "tags", "operationId", "deprecated", "security")} == {
        "summary": "Echo the request",
        "tags": ["debug"],
        "operationId": "postEcho",
        "deprecated": True,
        "security": [{"Bearer": []}],
    }
    assert op["parameters"][0] == {"name": "session", "in": "cookie", "required": True, "schema": {"type": "string"}}
    assert doc["servers"] == [{"url": "https://api.example.com"}]
    assert doc["components"]["securitySchemes"] == {"Bearer": {"type": "http", "scheme": "bearer"}}
    # Merged with the given components; Session's fields are parameters, so it is no schema.
    assert set(doc["components"]["schemas"]) == {"Echo", "ValidationProblem", "ValidationErrorItem"}


def test_the_served_document_follows_new_routes() -> None:
    app = TakibiAPI().openapi(getTodoRoute, getTodoRouteHandler)
    app.doc("/doc", {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}})
    assert set(run(app, "/doc").json()["paths"]) == {"/todos/{todoId}"}
    app.openapi(postEchoRoute, postEchoRouteHandler)
    assert set(run(app, "/doc").json()["paths"]) == {"/todos/{todoId}", "/echo"}


def test_mounted_middleware_is_prefixed() -> None:
    seen: list[str] = []

    async def mark(c: Context[Any], next: Next):
        seen.append(c.req.path)
        return await next()

    v1 = TakibiAPI().openapi(getTodoRoute, getTodoRouteHandler)
    v1.use(mark)  # all of v1: /v1/* once mounted
    app = TakibiAPI().route("/v1", v1).openapi(listTodosRoute, listTodosRouteHandler)
    run(app, "/v1/todos/1")
    run(app, "/todos")
    assert seen == ["/v1/todos/1"]


def test_a_body_rust_cannot_read_is_read_by_python() -> None:
    # A lone surrogate escape: json.loads keeps it in the str, which Rust strings cannot hold.
    res = run(
        make_app(),
        "/todos",
        method="POST",
        body='{"title": "\\ud800"}',
        headers={**HEADERS, "content-type": "application/json"},
    )
    assert (res.status, res.json()["title"]) == (201, "\ud800")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
