"""``takibiapi.cookie``: cookies read, set, deleted and signed, in the format takibi (npm) uses."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import pytest

from takibiapi import Context, Response, TakibiAPI, createRoute
from takibiapi.cookie import (
    deleteCookie,
    generateCookie,
    generateSignedCookie,
    getCookie,
    getSignedCookie,
    parse,
    parseSigned,
    serialize,
    setCookie,
    setSignedCookie,
)

SECRET = "secret"
# HMAC-SHA256("secret", "hello"), base64: the same bytes takibi (npm) signs with.
HELLO_SIGNATURE = "iKqz7ejTrflNJquQ07r9SiCDBww7zOnAFO4EpEOEfAs="


@dataclass
class Seen:
    theme: str | None
    all: dict[str, str]
    session: Any
    secure: str | None


class getSeenRoute(
    createRoute(
        {
            "method": "get",
            "path": "/seen",
            "responses": {200: {"description": "What came in", "content": {"application/json": {"schema": Seen}}}},
        }
    )
):
    """Read cookies, set two, delete one."""


def getSeenRouteHandler(c: Context[getSeenRoute]):
    seen = Seen(
        getCookie(c, "theme"), getCookie(c), getSignedCookie(c, SECRET, "session"), getCookie(c, "token", "secure")
    )
    setCookie(c, "theme", "dark", {"maxAge": 86400, "httpOnly": True, "sameSite": "lax"})
    setSignedCookie(c, "session", "42", SECRET)
    deleteCookie(c, "old")
    return c.json(seen)


class getRawRoute(
    createRoute({"method": "get", "path": "/raw", "responses": {200: {"description": "A plain Response"}}})
):
    """Set a cookie and return a Response built by hand."""


def getRawRouteHandler(c: Context[getRawRoute]):
    setCookie(c, "a", "1")
    return c.body(b"raw")


app = TakibiAPI().openapi(getSeenRoute, getSeenRouteHandler).openapi(getRawRoute, getRawRouteHandler)


def run(path: str, cookie: str | None = None) -> Response:
    headers = {"cookie": cookie} if cookie is not None else None
    return asyncio.run(app.request(path, headers=headers))


def test_reads_the_request_cookies_and_sets_several() -> None:
    hello = f"session={_signed('42')}; theme=light; __Secure-token=t; other=%E2%9C%93"
    res = run("/seen", hello)
    assert res.json() == {
        "theme": "light",
        "all": {"session": f"42.{_sign('42')}", "theme": "light", "__Secure-token": "t", "other": "✓"},
        "session": "42",
        "secure": "t",
    }
    cookies = res.headers["set-cookie"]
    assert isinstance(cookies, list)
    assert cookies == [
        "theme=dark; Max-Age=86400; Path=/; HttpOnly; SameSite=Lax",
        f"session=42.{_encoded_sign('42')}; Path=/",
        "old=; Max-Age=0; Path=/",
    ]


def test_without_cookies() -> None:
    assert run("/seen").json() == {"theme": None, "all": {}, "session": None, "secure": None}


def test_a_wrong_signature_is_false() -> None:
    res = run("/seen", f"session=42.{HELLO_SIGNATURE}")
    assert res.json()["session"] is False


def test_set_cookie_reaches_a_response_built_by_hand() -> None:
    res = run("/raw")
    assert (res.text(), res.headers["set-cookie"]) == ("raw", "a=1; Path=/")


def test_signature_matches_the_npm_package() -> None:
    assert generateSignedCookie("greeting", "hello", SECRET) == f"greeting=hello.{_encoded(HELLO_SIGNATURE)}; Path=/"
    assert parseSigned(f"greeting=hello.{_encoded(HELLO_SIGNATURE)}", SECRET) == {"greeting": "hello"}
    assert parseSigned(f"greeting=hello.{_encoded(HELLO_SIGNATURE)}", "other") == {"greeting": False}
    assert parseSigned("greeting=no-signature", SECRET) == {}
    assert parseSigned(f"greeting=hello.{_encoded(HELLO_SIGNATURE)}", b"secret") == {"greeting": "hello"}


def test_parse() -> None:
    assert parse('a=1; b="quoted"; c=%20x; d; paraglide:lang=en; a=2; bad name=1; e=has"quote') == {
        "a": "1",
        "b": "quoted",
        "c": " x",
        "paraglide:lang": "en",
    }
    assert parse("a=1; b=2", "b") == {"b": "2"}
    assert parse("a=1", "zzz") == {}


def test_serialize_attributes_and_prefixes() -> None:
    when = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0)  # within the 400 days a cookie may live
    assert (
        serialize(
            "s",
            "a b",
            {
                "domain": "example.com",
                "path": "/x",
                "expires": when,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Strict",
                "priority": "high",
                "partitioned": True,
                "maxAge": 10,
            },
        )
        == f"s=a%20b; Max-Age=10; Domain=example.com; Path=/x; Expires={format_datetime(when, usegmt=True)}; "
        "HttpOnly; Secure; SameSite=Strict; Priority=High; Partitioned"
    )
    assert generateCookie("t", "1", {"prefix": "secure"}) == "__Secure-t=1; Path=/; Secure"
    assert (
        generateCookie("t", "1", {"prefix": "host", "domain": "ignored.example", "path": "/x"})
        == "__Host-t=1; Path=/; Secure"
    )
    assert serialize("plain", "1") == "plain=1"


@pytest.mark.parametrize(
    ("name", "options", "message"),
    [
        ("bad name", {}, "Invalid cookie name"),
        ("__Secure-x", {}, "__Secure- Cookie must have Secure"),
        ("__Host-x", {"secure": True, "path": "/a"}, '__Host- Cookie must have Path attributes with "/"'),
        ("__Host-x", {"secure": True, "path": "/", "domain": "a"}, "__Host- Cookie must not have Domain"),
        ("x", {"maxAge": 34560001}, "Max-Age SHOULD NOT be greater than 400 days"),
        ("x", {"expires": datetime.now(UTC) + timedelta(days=401)}, "Expires SHOULD NOT be greater than 400 days"),
        ("x", {"partitioned": True}, "Partitioned Cookie must have Secure"),
        ("x", {"path": "/;evil"}, 'path must not contain ";"'),
    ],
)
def test_serialize_refuses(name: str, options: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        serialize(name, "v", options)


def _sign(value: str) -> str:
    import base64
    import hashlib
    import hmac

    return base64.b64encode(hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).digest()).decode()


def _encoded(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="!*'()")


def _encoded_sign(value: str) -> str:
    return _encoded(_sign(value))


def _signed(value: str) -> str:
    return _encoded(f"{value}.{_sign(value)}")
