# TakibiAPI

REST APIs whose OpenAPI document, request checks and handler types come from Python's own types (dataclasses, `TypedDict`, `Literal`, `Enum`, `Annotated`). No runtime dependencies; JSON, routing and type checks run in a Rust extension. ASGI and Granian's RSGI.

```sh
pip install takibiapi
```

## Quick start

```python
from dataclasses import dataclass

from takibiapi import Context, TakibiAPI, createRoute


@dataclass
class Message:
    message: str


class getTakibiRoute(
    createRoute(
        {
            "method": "get",
            "path": "/takibi",
            "responses": {200: {"description": "takibi🔥", "content": {"application/json": {"schema": Message}}}},
        }
    )
):
    """Say takibi🔥."""


def getTakibiRouteHandler(c: Context[getTakibiRoute]):
    return c.json(Message("takibi🔥"))


app = TakibiAPI()
app.openapi(getTakibiRoute, getTakibiRouteHandler)
app.doc("/doc", {"openapi": "3.1.0", "info": {"title": "takibi API", "version": "1.0.0"}})
```

```bash
granian --interface rsgi myapp:app   # or: uvicorn myapp:app
curl localhost:8000/takibi           # {"message":"takibi🔥"}
```

## Routes and handlers

A route is a class made with `createRoute`, and its handler takes `c: Context[thatRoute]`. `app.openapi(route, handler)` raises `TypeError` for anything else, and the type checker sees the route's types: `c.req.valid(...)` is the declared request type and `c.json(...)` takes only the declared responses.

The response's status is typed too. `c.json`, `c.text` and `c.body` take only a status the route declares (200 when none is given, so a route without 200 must name its status), and the handler must return what they build (a `TypedResponse`):

```python
"responses": {201: {...Todo}, 404: {...Problem}}

return c.json(Todo(...), 201)      # ok
return c.json(Todo(...), 200)      # type error: 200 is not declared
return c.body(status=204)          # type error: nor 204
return Response(b"...")            # type error at app.openapi: not a declared response
```

A `"default"` response allows any status. The statuses are inferred from the literal keys, so annotate shared responses with theirs: `errors: Mapping[Literal[400, 404], ResponseConfig[Problem]]`, spread as `{**errors, 200: ...}` (a `dict[int, ...]` is a type error). Which body goes with which status is not checked: `c.json(Todo(...), 404)` passes when both are declared.

## Requests

The request parts in `request` (`params`, `query`, `headers`, `cookies`, a JSON `body`) are checked against their **types** before the handler runs and read with `c.req.valid("param" | "query" | "header" | "cookie" | "json")`:

```python
params = c.req.valid("param")  # "/todos/7" -> {"todoId": 7}: text converted to the declared int
```

Only types are checked: `Annotated` keywords (`minLength`, `maximum`, ...) are written to the document but not checked, and responses are not checked at run time. For those, use [pydantic-takibiapi](../pydantic-takibiapi) or [zopy-takibiapi](../zopy-takibiapi).

A wrong type gets **422** Problem Details (`errors: [{in, path, code, message}]`), invalid JSON **400**, a required body with another Content-Type **415**.

## Errors

```python
app.onError(lambda c: c.json({"error": str(c.error)}, 500))  # exceptions from handlers and middleware
app.notFound(lambda c: c.text("not here", 404))  # paths with no route
```

Both take handlers `def h(c)` (the exception is `c.error`) and middleware `async def mw(c, next)`, and registrations compose instead of replacing each other: middleware runs around the handlers registered after it (`await next()` is their response, `None` when none answered), a handler ends the chain by returning a response, and `prefix="/api"` limits a registration to those paths. The handlers of a mounted app (`app.route("/v1", v1)`) come first for its paths, then the parent's in registration order.

```python
async def report(c, next):
    res = await next()
    log.warning("%s %s -> %s", c.req.method, c.req.path, res.status if res else "default")
    return res


app.onError(report, lambda c: c.text("API failed", 500), prefix="/api")
app.notFound(report, lambda c: c.text("no such API", 404), prefix="/api")
```

Without them, Takibi answers 500 / 404 Problem Details (and 405 with `Allow` for a known path), which also answer when a chain ends without a response.

## Middleware and CORS

`app.use(middleware, prefix="/api")` runs `async def mw(c, next)` around the handlers. CORS, as Hono's `hono/cors`:

```python
from takibiapi.cors import cors

app.use(cors())  # Access-Control-Allow-Origin: *
app.use(cors({"origin": ["https://a.example"], "credentials": True, "maxAge": 600}), prefix="/api")
```

Options: `origin` (`"*"`, an origin, a list, or `(origin, c) -> origin | None`), `allowMethods`, `allowHeaders`, `exposeHeaders`, `maxAge`, `credentials`. Preflights (`OPTIONS`) are answered with 204.

## Cookies

`takibiapi.cookie` reads and sets cookies, plain or signed:

```python
from takibiapi.cookie import deleteCookie, getCookie, getSignedCookie, setCookie, setSignedCookie


def getProfileRouteHandler(c: Context[getProfileRoute]):
    theme = getCookie(c, "theme")  # str | None; getCookie(c) is every cookie as a dict
    session = getSignedCookie(c, secret, "session")  # the value, False if tampered with, None if absent
    setCookie(c, "theme", "dark", {"maxAge": 86400, "httpOnly": True, "sameSite": "Lax"})
    setSignedCookie(c, "session", "42", secret)
    deleteCookie(c, "old")  # Max-Age=0; returns the value the request carried
    return c.json(profile)
```

`setCookie` adds a `Set-Cookie` header to the response the handler returns (`c.header(name, value, append=True)` underneath); `path` is `/` unless given, and the options are the header's attributes (`domain`, `expires`, `httpOnly`, `maxAge`, `path`, `secure`, `sameSite`, `priority`, `partitioned`) plus `prefix`: `"secure"` names the cookie `__Secure-<name>`, `"host"` `__Host-<name>`, each with the attributes it requires. A signed cookie carries an HMAC-SHA256 of its value, in the format Takibi (npm) uses, so the two read each other's cookies with the same secret. `generateCookie` / `generateSignedCookie` give the header value alone.

## OpenAPI

`app.doc(path, config)` serves the document and `app.getOpenAPIDocument(config)` returns it; `config` must give `openapi` (3.0.x or 3.1.x) and `info`. Dataclasses, `TypedDict`s and `Enum`s become `components.schemas`. Swagger UI: [takibi-swagger-ui](../takibi-swagger-ui).

## Development

From the repository root: `uv sync` builds the extension into the virtualenv, and `uv run pytest packages/takibiapi` runs this package's tests (on both the Rust engine and the pure-Python reference). See the root README.

## License

Distributed under the MIT License. See [LICENSE](https://github.com/nakita628/takibiapi/blob/main/packages/takibiapi/LICENSE) for more information.
