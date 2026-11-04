"""``cors``: the CORS middleware, as Hono's ``hono/cors``.

    from takibiapi.cors import cors

    app.use(cors())                                              # Access-Control-Allow-Origin: *
    app.use(cors({"origin": "https://example.com", "credentials": True}), prefix="/api")

A preflight (``OPTIONS``) is answered here with 204; other requests get the headers on the
handler's response, error responses included.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypedDict

from .app import Middleware, Next
from .context import Context, Response

OriginResolver = Callable[[str, Context[Any]], str | Awaitable[str | None] | None]
MethodsResolver = Callable[[str, Context[Any]], Sequence[str] | Awaitable[Sequence[str]]]


class CORSOptions(TypedDict, total=False):
    # "*" (default), one origin, a list of them, or (origin, c) -> the origin to allow or None.
    origin: str | Sequence[str] | OriginResolver
    # Default: GET, HEAD, PUT, POST, DELETE, PATCH; or (origin, c) -> methods.
    allowMethods: Sequence[str] | MethodsResolver
    # Default: the request's Access-Control-Request-Headers.
    allowHeaders: Sequence[str]
    maxAge: int
    credentials: bool
    exposeHeaders: Sequence[str]


_DEFAULT_METHODS = ("GET", "HEAD", "PUT", "POST", "DELETE", "PATCH")


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _append(headers: dict[str, str | list[str]], name: str, value: str) -> None:
    current = headers.get(name)
    headers[name] = value if not current else f"{current}, {value}"


def cors(options: CORSOptions | None = None) -> Middleware:
    """The CORS middleware for ``app.use`` (see the module docstring)."""
    opts: CORSOptions = {**(options or {})}
    origin_option = opts.get("origin", "*")
    if isinstance(origin_option, str):
        allowed = origin_option
        find_origin: OriginResolver = (
            (lambda _origin, _c: "*") if allowed == "*" else (lambda origin, _c: origin if origin == allowed else None)
        )
    elif callable(origin_option):
        find_origin = origin_option
    else:
        allowed_list = list(origin_option)
        find_origin = lambda origin, _c: origin if origin in allowed_list else None  # noqa: E731
    methods_option = opts.get("allowMethods", _DEFAULT_METHODS)
    if callable(methods_option):
        find_methods: MethodsResolver = methods_option
    else:
        methods = list(methods_option)
        find_methods = lambda _origin, _c: methods  # noqa: E731
    vary_origin = origin_option != "*"
    credentials = opts.get("credentials", False)
    expose = list(opts.get("exposeHeaders", ()))
    allow_headers = list(opts.get("allowHeaders", ()))
    max_age = opts.get("maxAge")

    async def middleware(c: Context[Any], next: Next) -> Response:
        request_origin = c.req.header("origin") or ""
        headers: dict[str, str | list[str]] = {}
        allow_origin = await _resolve(find_origin(request_origin, c))
        if allow_origin:
            headers["access-control-allow-origin"] = allow_origin
        if credentials:
            headers["access-control-allow-credentials"] = "true"
        if expose:
            headers["access-control-expose-headers"] = ",".join(expose)

        if c.req.method == "OPTIONS":  # preflight
            if vary_origin:
                headers["vary"] = "Origin"
            if max_age is not None:
                headers["access-control-max-age"] = str(max_age)
            methods = list(await _resolve(find_methods(request_origin, c)))
            if methods:
                headers["access-control-allow-methods"] = ",".join(methods)
            requested = allow_headers
            if not requested:
                raw = c.req.header("access-control-request-headers")
                requested = [h.strip() for h in raw.split(",") if h.strip()] if raw else []
            if requested:
                headers["access-control-allow-headers"] = ",".join(requested)
                _append(headers, "vary", "Access-Control-Request-Headers")
            return Response(b"", 204, headers)

        res = await next()
        res.headers.update(headers)
        if vary_origin:
            _append(res.headers, "vary", "Origin")
        return res

    return middleware


__all__ = ["CORSOptions", "cors"]
