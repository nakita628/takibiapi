"""The handler's ``c``: the request (``c.req``) and the response builders (``c.json`` / ...)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, overload

from .route import AnyRoute, Route
from .schema import ValidationError, dumps, loads, parse_query

if TYPE_CHECKING:
    from .schemas import Encoder

Receive = Callable[[], Awaitable[dict[str, Any]]]
# ASGI's (bytes, bytes) pairs, or an RSGI headers object (``.items()`` of str pairs).
RawHeaders = list[tuple[bytes, bytes]] | Any

# The route a context belongs to. Covariant, so ``Context[getTodoRoute]`` (a subclass of
# ``Route[TodoIdParams, ...]``) matches the self-typed overloads below.
TR = TypeVar("TR", bound=AnyRoute, covariant=True)
_P = TypeVar("_P")
_Q = TypeVar("_Q")
_J = TypeVar("_J")
_H = TypeVar("_H")
_B = TypeVar("_B")

JSON_TYPE = "application/json"
PROBLEM_JSON = "application/problem+json"


# A header's value; a list sends the header once per item (several ``Set-Cookie``).
HeaderValue = str | list[str]


class Response:
    """What a handler returns: body bytes, status and headers (``c.json(...)`` builds one)."""

    __slots__ = ("body", "headers", "status")

    def __init__(self, body: bytes = b"", status: int = 200, headers: Mapping[str, HeaderValue] | None = None) -> None:
        self.body = body
        self.status = status
        self.headers: dict[str, HeaderValue] = (
            {} if headers is None else headers if isinstance(headers, dict) else dict(headers)
        )

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None

    def text(self) -> str:
        return self.body.decode()

    def __repr__(self) -> str:
        return f"Response({self.status}, {self.body[:60]!r})"


def problem(status: int, title: str, detail: str, instance: str, type_: str = "about:blank", **extra: Any) -> Response:
    """An RFC 9457 Problem Details response (``application/problem+json``)."""
    body = {"type": type_, "title": title, "status": status, "detail": detail, "instance": instance, **extra}
    return Response(dumps(body), status, {"content-type": PROBLEM_JSON})


class ResponseValidationError(Exception):
    """A handler's ``c.json(...)`` does not match the type the route declares for that status
    (only with schemas that check responses). The server broke its own contract: a 500."""

    def __init__(self, status: int, media_type: str, issues: list[dict[str, Any]]) -> None:
        super().__init__(f"response {status} {media_type} does not match the route's schema: {issues}")
        self.status = status
        self.media_type = media_type
        self.issues = issues


class MalformedJSONError(ValueError):
    """``await c.req.json()`` on a body that is not JSON; the app answers 400 Bad Request."""


class Request(Generic[TR]):
    """The request. ``valid(...)`` gives the parts the route declares, checked against their types
    (and converted: ``"1"`` -> ``1`` for an ``int`` parameter) before the handler runs; the other
    methods give the raw request (parameters as the strings that were sent)."""

    __slots__ = (
        "_body",
        "_headers",
        "_json",
        "_params",
        "_query",
        "_query_string",
        "_raw_headers",
        "_receive",
        "_valid",
        "method",
        "path",
    )

    def __init__(
        self,
        method: str,
        path: str,
        params: dict[str, str],
        query_string: bytes,
        raw_headers: RawHeaders,
        receive: Receive | None,
    ) -> None:
        self.method = method
        self.path = path
        self._params = params
        self._query_string = query_string
        self._query: dict[str, list[str]] | None = None
        self._raw_headers = raw_headers
        self._headers: dict[str, str] | None = None
        self._receive = receive
        self._body: bytes | None = None
        self._json: Any = _UNSET
        self._valid: dict[str, Any] = {}

    # The checked part's type comes from the route: Route[params, query, json, headers, ...].
    @overload
    def valid(self: Request[Route[_P, Any, Any, Any, Any]], target: Literal["param"]) -> _P: ...
    @overload
    def valid(self: Request[Route[Any, _Q, Any, Any, Any]], target: Literal["query"]) -> _Q: ...
    @overload
    def valid(self: Request[Route[Any, Any, _J, Any, Any]], target: Literal["json"]) -> _J: ...
    @overload
    def valid(self: Request[Route[Any, Any, Any, _H, Any]], target: Literal["header"]) -> _H: ...
    @overload
    def valid(self, target: Literal["cookie"]) -> Any: ...

    def valid(self, target: str) -> Any:
        """The request part the route declares (``"param"``, ``"query"``, ``"json"``,
        ``"header"``, ``"cookie"``), checked against its type: a dataclass instance, a dict for a
        ``TypedDict``, ... Only types are checked, never ``Annotated`` keywords."""
        try:
            return self._valid[target]
        except KeyError:
            raise KeyError(f"no {target!r} type in this route's request config") from None

    @overload
    def param(self) -> dict[str, str]: ...
    @overload
    def param(self, name: str) -> str: ...

    def param(self, name: str | None = None) -> Any:
        """Path parameters: ``/todos/{todoId}`` -> ``c.req.param("todoId")`` (always present)."""
        return dict(self._params) if name is None else self._params[name]

    def _queries(self) -> dict[str, list[str]]:
        if self._query is None:
            self._query = parse_query(self._query_string)
        return self._query

    @overload
    def query(self) -> dict[str, str]: ...
    @overload
    def query(self, name: str) -> str | None: ...

    def query(self, name: str | None = None) -> Any:
        """The first value of each query key."""
        if name is None:
            return {k: v[0] for k, v in self._queries().items()}
        values = self._queries().get(name)
        return values[0] if values else None

    @overload
    def queries(self) -> dict[str, list[str]]: ...
    @overload
    def queries(self, name: str) -> list[str]: ...

    def queries(self, name: str | None = None) -> Any:
        """Every value of each query key: ``?tag=a&tag=b`` -> ``["a", "b"]``."""
        if name is None:
            return {k: list(v) for k, v in self._queries().items()}
        return list(self._queries().get(name, ()))

    @overload
    def header(self) -> dict[str, str]: ...
    @overload
    def header(self, name: str) -> str | None: ...

    def header(self, name: str | None = None) -> Any:
        """Request headers; names are lowercase."""
        if self._headers is None:
            raw: Any = self._raw_headers
            if type(raw) is list:
                self._headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in raw}
            else:  # RSGI (Granian): already text
                self._headers = {k.lower(): v for k, v in raw.items()}
        return dict(self._headers) if name is None else self._headers.get(name.lower())

    @overload
    def cookie(self) -> dict[str, str]: ...
    @overload
    def cookie(self, name: str) -> str | None: ...

    def cookie(self, name: str | None = None) -> Any:
        jar: dict[str, str] = {}
        for raw in (self.header("cookie") or "").split(";"):
            key, sep, value = raw.strip().partition("=")
            if sep:
                jar[key] = value
        return jar if name is None else jar.get(name)

    async def body(self) -> bytes:
        """The raw request body."""
        if self._body is None:
            chunks: list[bytes] = []
            more = self._receive is not None
            while more:
                assert self._receive is not None
                message = await self._receive()
                chunks.append(message.get("body", b""))
                more = message.get("more_body", False)
            self._body = b"".join(chunks)
        return self._body

    async def text(self) -> str:
        return (await self.body()).decode()

    async def json(self) -> Any:
        """The body parsed as JSON (not checked: use ``c.req.valid("json")`` for the checked one)."""
        if self._json is _UNSET:
            data = await self.body()
            try:
                self._json = loads(data) if data else None
            except ValueError as exc:
                raise MalformedJSONError(str(exc)) from None
        return self._json


_UNSET: Any = object()


class Context(Generic[TR]):
    """``c`` in a handler. ``Context[getTodoRoute]`` types ``c.req.valid(...)`` from the route's
    request types and ``c.json(...)`` from its responses."""

    __slots__ = ("_headers", "_responses", "_vars", "error", "req")

    def __init__(self, req: Request[TR], responses: Mapping[int, Mapping[str, Encoder]] | None = None) -> None:
        self.req = req
        self.error: Exception | None = None  # the exception an ``onError`` handler answers
        self._headers: dict[str, HeaderValue] | None = None
        self._vars: dict[str, Any] | None = None
        self._responses = responses  # {status: {media type: encoder}}: checked responses

    def json(
        self: Context[Route[Any, Any, Any, Any, _B]],
        value: _B,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        """A JSON response. ``value`` is typed by the route's responses (for the type checker).
        With takibi's own schemas it is written as it is; schemas that check responses
        (``pydantic-takibiapi``) raise :class:`ResponseValidationError` when it does not match."""
        if self._responses is not None:
            encoders = self._responses.get(status)
            if encoders:
                media_type = JSON_TYPE
                if headers:
                    for k, v in headers.items():
                        if k.lower() == "content-type":
                            media_type = v.split(";", 1)[0].strip().lower()
                encoder = encoders.get(media_type)
                if encoder is not None:
                    try:
                        body = encoder.encode_json(value)
                    except ValidationError as exc:
                        raise ResponseValidationError(status, media_type, exc.issues) from None
                    return self._respond(body, status, headers, JSON_TYPE)
        return self._respond(dumps(value), status, headers, JSON_TYPE)

    def text(self, text: str, status: int = 200, headers: Mapping[str, str] | None = None) -> Response:
        return self._respond(text.encode(), status, headers, "text/plain; charset=utf-8")

    def body(self, data: bytes = b"", status: int = 200, headers: Mapping[str, str] | None = None) -> Response:
        """Raw bytes (``c.body(status=204)`` for no content); set the content type in ``headers``."""
        return self._respond(data, status, headers, None)

    def header(self, name: str, value: str | None, *, append: bool = False) -> None:
        """Set a header on the response the handler returns; ``append=True`` adds a value to the
        ones already set (a second ``Set-Cookie`` is sent as its own header), ``None`` removes it."""
        name = name.lower()
        if self._headers is None:
            self._headers = {}
        if value is None:
            self._headers.pop(name, None)
            return
        current = self._headers.get(name) if append else None
        if current is None:
            self._headers[name] = value
        elif isinstance(current, list):
            current.append(value)
        elif name == "set-cookie":
            self._headers[name] = [current, value]
        else:
            self._headers[name] = f"{current}, {value}"

    def set(self, key: str, value: Any) -> None:
        """Store a value for this request (e.g. the user a middleware authenticated)."""
        if self._vars is None:
            self._vars = {}
        self._vars[key] = value

    def get(self, key: str) -> Any:
        return None if self._vars is None else self._vars.get(key)

    def _respond(
        self, payload: bytes, status: int, headers: Mapping[str, str] | None, content_type: str | None
    ) -> Response:
        merged: dict[str, HeaderValue] = {} if content_type is None else {"content-type": content_type}
        if self._headers:
            merged.update(self._headers)
        if headers:
            merged.update({k.lower(): v for k, v in headers.items()})
        return Response(payload, status, merged)
