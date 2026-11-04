"""``TakibiAPI``: a REST app on ASGI whose routes are OpenAPI operations described with Python types.

Run it with any ASGI server (``uvicorn module:app``), with Granian's faster RSGI interface
(``granian --interface rsgi module:app``), or call ``await app.request("/path")`` in tests. Request
parts are checked against their types before the handler runs (``c.req.valid``); ``Annotated``
keywords and responses are not checked.
"""

from __future__ import annotations

import inspect
import logging
import typing
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Self, TypeVar, get_args, get_origin

from .context import Context, MalformedJSONError, RawHeaders, Request, Response, TypedResponse, problem
from .openapi import OpenAPIConfig, build_document, check_config
from .route import AnyRoute, route_class
from .router import new_router
from .schema import ValidationError, dumps
from .schemas import Checker, Encoder, Schemas, TypeSchemas

Handler = Callable[[Context[Any]], Response | Awaitable[Response]]
Next = Callable[[], Awaitable[Response]]
# ``async def mw(c, next): ...; res = await next(); ...; return res`` (return early to stop).
Middleware = Callable[[Context[Any], Next], Awaitable[Response]]
# ``onError`` / ``notFound`` take handlers ``def h(c)`` (the error is ``c.error``) and middleware
# ``async def mw(c, next)``; ``next()`` runs the rest of the chain and is ``None`` when it ends
# without an answer, which the built-in response then gives.
FallbackNext = Callable[[], Awaitable[Response | None]]
FallbackMiddleware = Callable[[Context[Any], FallbackNext], Awaitable[Response | None]]
FallbackHandler = Callable[[Context[Any]], Response | Awaitable[Response | None] | None]
Fallback = FallbackHandler | FallbackMiddleware

# The registered route; its handler takes ``c: Context[that route]``.
_TR = TypeVar("_TR", bound=AnyRoute)

# Where each checked part lives, as an OpenAPI parameter location ("in") for error reports.
_LOCATIONS = {"param": "path", "query": "query", "header": "header", "cookie": "cookie", "json": "body"}
VALIDATION_PROBLEM_TYPE = "/problems/validation-error"

log = logging.getLogger("takibiapi")


class _Entry:
    __slots__ = ("checks", "handler", "is_async", "responses")

    def __init__(
        self,
        handler: Callable[..., Any],
        checks: list[tuple[str, Checker, bool]] | None = None,
        responses: dict[int, dict[str, Encoder]] | None = None,
    ) -> None:
        self.handler = handler
        self.is_async = inspect.iscoroutinefunction(handler)
        self.checks = checks or []  # (target, checker, required) for c.req.valid
        self.responses = responses  # {status: {media type: encoder}} for c.json, or None


class _Fallback:
    """One ``onError`` / ``notFound`` registration: runs for paths under ``prefix``; handlers of
    more deeply mounted apps (``depth``) come first, then registration order."""

    __slots__ = ("depth", "fn", "is_middleware", "prefix")

    def __init__(self, fn: Fallback, prefix: str, depth: int = 0) -> None:
        self.fn: Callable[..., Any] = fn  # called as ``fn(c)`` or ``fn(c, next)``, by is_middleware
        self.prefix = prefix
        self.depth = depth
        self.is_middleware = _arity(fn) >= 2

    def mounted(self, prefix: str) -> _Fallback:
        return _Fallback(self.fn, prefix + self.prefix, self.depth + 1)


class TakibiAPI:
    """``app = TakibiAPI()``; ``app.openapi(route, handler)``; ``app.doc("/doc", {...})``."""

    # How the types in routes become checks and OpenAPI schemas (see takibiapi.schemas).
    schemas: Schemas = TypeSchemas()

    def __init__(self) -> None:
        self._router = new_router()
        self._registrations: list[tuple[str, str, _Entry]] = []
        self._routes: list[type[AnyRoute]] = []
        self._middleware: list[tuple[str, Middleware]] = []
        self._on_error_fallbacks: list[_Fallback] = []
        self._not_found_fallbacks: list[_Fallback] = []
        self._documents: dict[int, bytes] = {}

    # -- routes ------------------------------------------------------------------------------- #
    def openapi(
        self, route: type[_TR], handler: Callable[[Context[_TR]], TypedResponse | Awaitable[TypedResponse]]
    ) -> Self:
        """Register ``route`` with its handler.

        The route must be declared as a class (``class getTodoRoute(createRoute({...}))``) and
        the handler must take it as ``c: Context[getTodoRoute]``; anything else is a ``TypeError``.
        For the type checker, the handler returns what ``c.json`` / ``c.text`` / ``c.body`` build,
        with a status the route declares.
        The request parts the route declares are checked against their types before the handler
        runs (422 when they do not match) and read with ``c.req.valid(...)``.
        """
        _check_handler(route, handler)
        request = route.config.get("request") or {}
        checks: list[tuple[str, Checker, bool]] = []
        for key, target in (("params", "param"), ("query", "query"), ("headers", "header"), ("cookies", "cookie")):
            if request.get(key) is not None:
                checks.append((target, self.schemas.checker(request[key], target), True))
        body = request.get("body") or {}
        for media_type, media in (body.get("content") or {}).items():
            if "json" in media_type and media.get("schema") is not None:
                checks.append(
                    ("json", self.schemas.checker(media["schema"], "json"), bool(body.get("required", False)))
                )
        responses: dict[int, dict[str, Encoder]] = {}
        for status, response in (route.config.get("responses") or {}).items():
            for media_type, media in (response.get("content") or {}).items():
                if media.get("schema") is None or not str(status).isdigit():
                    continue
                encoder = self.schemas.response_encoder(media["schema"])
                if encoder is not None:
                    responses.setdefault(int(status), {})[media_type.lower()] = encoder
        self._add(route.method, route.path, _Entry(handler, checks, responses or None))
        self._routes.append(route)
        self._documents.clear()
        return self

    def route(self, prefix: str, app: TakibiAPI) -> Self:
        """Mount ``app``'s routes and middleware under ``prefix`` (``app.route("/v1", v1)``)."""
        if type(app.schemas) is not type(self.schemas):
            raise TypeError(
                f"cannot mount a {type(app).__name__} app with other schemas into a {type(self).__name__} app"
            )
        prefix = prefix.rstrip("/")
        for method, path, entry in app._registrations:
            self._add(method, prefix + path, entry)
        for pattern, middleware in app._middleware:
            self._middleware.append((prefix + pattern, middleware))
        self._on_error_fallbacks.extend(f.mounted(prefix) for f in app._on_error_fallbacks)
        self._not_found_fallbacks.extend(f.mounted(prefix) for f in app._not_found_fallbacks)
        for route in app._routes:
            self._routes.append(route_class({**route.config, "path": prefix + route.path}))
        self._documents.clear()
        return self

    def use(self, middleware: Middleware, *, prefix: str = "") -> Self:
        """Run ``middleware`` around the handlers (only under ``prefix`` if given)::

        async def cors(c, next):
            res = await next()
            res.headers["access-control-allow-origin"] = "*"
            return res
        """
        self._middleware.append((prefix.rstrip("/"), middleware))
        return self

    def onError(self, *handlers: Fallback, prefix: str = "") -> Self:  # noqa: N802
        """Answer for exceptions raised by handlers and middleware, as Hono's ``app.onError``::

            app.onError(lambda c: c.json({"error": str(c.error)}, 500))

        The exception is ``c.error``. Registrations compose rather than replace each other:
        ``async def mw(c, next)`` runs around the ones registered after it (``await next()`` is
        their answer, ``None`` when there is none), a ``def h(c)`` handler ends the chain by
        returning a response, and when the chain ends without one the built-in 500 Problem
        Details answers. ``prefix`` limits the handlers to paths under it. The handlers of a
        mounted app (``app.route``) come first for its paths; at the same depth, registration
        order. An exception from a handler here is logged and answered by the built-in response.
        """
        self._on_error_fallbacks.extend(_Fallback(fn, prefix.rstrip("/")) for fn in handlers)
        return self

    def notFound(self, *handlers: Fallback, prefix: str = "") -> Self:  # noqa: N802
        """Answer for paths with no route (default: 404 Problem Details; a known path with another
        method answers 405 before this). Composes like :meth:`onError`::

            app.notFound(lambda c: c.text("not here", 404))
            app.notFound(mw, lambda c: c.text("no such API", 404), prefix="/api")
        """
        self._not_found_fallbacks.extend(_Fallback(fn, prefix.rstrip("/")) for fn in handlers)
        return self

    # -- OpenAPI documents ---------------------------------------------------------------------- #
    def getOpenAPIDocument(self, config: OpenAPIConfig) -> dict[str, Any]:  # noqa: N802
        """The OpenAPI document for the ``openapi`` version given in ``config`` (3.0.x or 3.1.x)."""
        return build_document(self._routes, config, self.schemas)

    def doc(self, path: str, config: OpenAPIConfig) -> Self:
        """Serve the OpenAPI document at ``GET path`` (generated once, after the routes are known)."""
        check_config(config)
        key = id(config)

        def serve(c: Context[Any]) -> Response:
            body = self._documents.get(key)
            if body is None:
                body = self._documents[key] = dumps(build_document(self._routes, config, self.schemas))
            return Response(body, 200, {"content-type": "application/json"})

        self._add("GET", path, _Entry(serve))
        return self

    # -- dispatch ------------------------------------------------------------------------------- #
    def _add(self, method: str, path: str, entry: _Entry) -> None:
        self._router.add(method, path, entry)
        self._registrations.append((method.upper(), path, entry))

    async def handle(
        self,
        method: str,
        path: str,
        query_string: bytes = b"",
        raw_headers: RawHeaders | None = None,
        receive: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> Response:
        matched = self._router.match(method, path)
        if matched is None and method == "HEAD":
            matched = self._router.match("GET", path)
        entry, params = matched if matched is not None else (None, {})
        c: Context[Any] = Context(
            Request(method, path, params, query_string, raw_headers or [], receive),
            entry.responses if entry is not None else None,
        )
        if self._middleware:
            chain = [m for prefix, m in self._middleware if _under(prefix, path)]
            if chain:
                return await self._through(chain, 0, entry, c)
        return await self._dispatch(entry, c)

    async def _through(self, chain: list[Middleware], i: int, entry: _Entry | None, c: Context[Any]) -> Response:
        if i == len(chain):
            return await self._dispatch(entry, c)
        try:
            return await chain[i](c, lambda: self._through(chain, i + 1, entry, c))
        except Exception as exc:
            return await self._answer_error(exc, c)

    async def _dispatch(self, entry: _Entry | None, c: Context[Any]) -> Response:
        try:
            if entry is None:
                return await self._not_found(c)
            if entry.checks:
                failure = await _check_request(entry, c)
                if failure is not None:
                    return failure
            out = entry.handler(c)
            if entry.is_async:
                out = await out
            if not isinstance(out, Response):
                raise TypeError(
                    f"{entry.handler.__qualname__} returned {type(out).__name__}, not a Response (use c.json(...))"
                )
            return out
        except MalformedJSONError:  # RFC 9110 15.5.1
            return problem(400, "Bad Request", "The request body is not valid JSON.", c.req.path)
        except Exception as exc:
            return await self._answer_error(exc, c)

    async def _not_found(self, c: Context[Any]) -> Response:
        allowed = self._router.allowed(c.req.path)
        if allowed:  # RFC 9110 15.5.6
            res = problem(405, "Method Not Allowed", f"{c.req.method} is not allowed here.", c.req.path)
            res.headers["allow"] = ", ".join(allowed)
            return res
        out = await self._fallback(_chain(self._not_found_fallbacks, c.req.path), 0, c)
        if out is None:
            return problem(404, "Not Found", f"No route for {c.req.method} {c.req.path}.", c.req.path)
        return out

    async def _answer_error(self, exc: Exception, c: Context[Any]) -> Response:
        log.exception("%s %s: unhandled error", c.req.method, c.req.path, exc_info=exc)
        c.error = exc
        chain = _chain(self._on_error_fallbacks, c.req.path)
        if chain:
            try:
                out = await self._fallback(chain, 0, c)
                if out is not None:
                    return out
            except Exception as failure:
                log.exception("%s %s: the error handler failed", c.req.method, c.req.path, exc_info=failure)
        return problem(500, "Internal Server Error", "An unexpected error occurred.", c.req.path)

    async def _fallback(self, chain: list[_Fallback], i: int, c: Context[Any]) -> Response | None:
        """Runs the ``onError`` / ``notFound`` handlers in ``chain`` from ``i``; ``None`` past the end."""
        if i == len(chain):
            return None
        fallback = chain[i]
        out = fallback.fn(c, lambda: self._fallback(chain, i + 1, c)) if fallback.is_middleware else fallback.fn(c)
        if inspect.isawaitable(out):
            out = await out
        if out is not None and not isinstance(out, Response):
            raise TypeError(f"{fallback.fn.__qualname__} returned {type(out).__name__}, not a Response")
        return out

    # -- ASGI ------------------------------------------------------------------------------------ #
    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind == "http":
            method = scope["method"]
            res = await self.handle(
                method, scope["path"], scope.get("query_string", b""), scope.get("headers"), receive
            )
            # Python's str.encode here is faster than a Rust helper (measured).
            headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in _header_items(res.headers)]
            headers.append((b"content-length", str(len(res.body)).encode()))
            await send({"type": "http.response.start", "status": res.status, "headers": headers})
            await send({"type": "http.response.body", "body": b"" if method == "HEAD" else res.body})
        elif kind == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    async def __rsgi__(self, scope: Any, protocol: Any) -> None:
        """Granian's RSGI interface (``granian --interface rsgi module:app``): no per-request ASGI
        message dicts, and one call sends the whole response."""
        if scope.proto != "http":
            return
        method = scope.method

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": await protocol(), "more_body": False}

        res = await self.handle(method, scope.path, scope.query_string.encode("latin-1"), scope.headers, receive)
        protocol.response_bytes(res.status, _header_items(res.headers), b"" if method == "HEAD" else res.body)

    async def request(
        self,
        path: str,
        *,
        method: str = "GET",
        json: Any = None,
        body: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        """Call the app without a server, e.g. in tests: ``await app.request("/todos/1")``."""
        path, _, query = path.partition("?")
        merged = {k.lower(): v for k, v in (headers or {}).items()}
        if json is not None:
            payload = dumps(json)
            merged.setdefault("content-type", "application/json")
        else:
            payload = body.encode() if isinstance(body, str) else (body or b"")
        raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in merged.items()]

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": payload, "more_body": False}

        return await self.handle(method.upper(), path, query.encode(), raw_headers, receive)


def _header_items(headers: Mapping[str, str | list[str]]) -> list[tuple[str, str]]:
    """The headers as (name, value) pairs: a list value (several ``Set-Cookie``) is one pair per item."""
    items: list[tuple[str, str]] = []
    for name, value in headers.items():
        if isinstance(value, list):
            items.extend((name, v) for v in value)
        else:
            items.append((name, value))
    return items


async def _check_request(entry: _Entry, c: Context[Any]) -> Response | None:
    """Fill ``c.req.valid(...)``; the error response when a part does not match its type."""
    req = c.req
    for target, checker, required in entry.checks:
        try:
            if target == "param":
                data = checker.decode(req._params, coerce=True)
            elif target == "query":
                queries = req._queries()
                raw = {name: v if repeats else v[0] for name, repeats in checker.names if (v := queries.get(name))}
                data = checker.decode(raw, coerce=True)
            elif target == "header":
                headers = req.header()
                raw = {name: headers[name.lower()] for name, _ in checker.names if name.lower() in headers}
                data = checker.decode(raw, coerce=True)
            elif target == "cookie":
                data = checker.decode(req.cookie(), coerce=True)
            else:
                content_type = req.header("content-type") or ""
                if "json" not in content_type:
                    if required:  # RFC 9110 15.5.16
                        detail = f"Content-Type {content_type or '(none)'} is not supported here"
                        return problem(415, "Unsupported Media Type", detail, req.path)
                    continue
                try:  # parsed and checked in one pass
                    data = checker.decode_json(await req.body())
                except ValueError:  # RFC 9110 15.5.1 (ValidationError is not a ValueError)
                    return problem(400, "Bad Request", "The request body is not valid JSON.", req.path)
        except ValidationError as exc:  # well-formed but of the wrong type: RFC 9110 15.5.21
            return _validation_problem(target, exc.issues, req.path)
        req._valid[target] = data
    return None


def _validation_problem(target: str, issues: list[dict[str, Any]], instance: str) -> Response:
    """422 Problem Details with one entry per issue (``ValidationProblem`` in the document)."""
    location = _LOCATIONS[target]
    errors = [
        {
            "in": location,
            "path": ".".join(str(p) for p in issue["path"]),
            "code": issue["code"],
            "message": issue["message"],
        }
        for issue in issues
    ]
    detail = f"The request {location} is invalid." if location == "body" else f"Invalid {location} parameters."
    return problem(422, "Unprocessable Content", detail, instance, VALIDATION_PROBLEM_TYPE, errors=errors)


def _check_handler(route: type[AnyRoute], handler: Callable[..., Any]) -> None:
    """Routes are named classes and handlers say which one they serve: ``c: Context[route]``."""
    if route._generated:
        raise TypeError(
            f"declare the route as a class to name it in the handler: "
            f"class getTodoRoute(createRoute({{...}})) (got {route.__name__})"
        )
    name = getattr(handler, "__qualname__", repr(handler))
    expected = f"def {route.__name__}Handler(c: Context[{route.__name__}])"
    params = list(inspect.signature(handler).parameters.values())
    if not params:
        raise TypeError(f"{name} takes no context; write it as {expected}")
    try:
        annotation = typing.get_type_hints(handler).get(params[0].name)
    except Exception as exc:
        raise TypeError(
            f"cannot resolve the annotations of {name} ({exc}); define {route.__name__} at module level"
        ) from exc
    if get_origin(annotation) is not Context or get_args(annotation) != (route,):
        raise TypeError(f"{name} must take the route's context: {expected}")


def _under(prefix: str, path: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")


def _chain(fallbacks: list[_Fallback], path: str) -> list[_Fallback]:
    """The handlers for ``path``: the more deeply mounted first, then in registration order."""
    return sorted((f for f in fallbacks if _under(f.prefix, path)), key=lambda f: -f.depth)


def _arity(fn: Callable[..., Any]) -> int:
    try:
        return len(inspect.signature(fn).parameters)
    except (TypeError, ValueError):  # a builtin or a callable without a signature: ``h(c)``
        return 1


__all__ = ["Fallback", "FallbackHandler", "FallbackMiddleware", "FallbackNext", "Middleware", "Next", "TakibiAPI"]
