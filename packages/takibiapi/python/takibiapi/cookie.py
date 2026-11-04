"""``takibiapi.cookie``: reading and setting cookies, plain or signed.

    from takibiapi.cookie import deleteCookie, getCookie, setCookie

    def getProfileRouteHandler(c: Context[getProfileRoute]):
        theme = getCookie(c, "theme")  # str | None; getCookie(c) is every cookie
        setCookie(c, "theme", "dark", {"maxAge": 86400, "httpOnly": True, "sameSite": "Lax"})
        return c.json(profile)

``setCookie`` adds a ``Set-Cookie`` header to the response the handler returns (``path`` is ``/``
unless given). ``prefix``: ``"secure"`` names the cookie ``__Secure-<name>`` and ``"host"``
``__Host-<name>``, with the attributes those prefixes require.

A signed cookie carries an HMAC-SHA256 of its value (``value.signature``, the signature base64),
so that a client cannot change it: ``setSignedCookie(c, "session", "42", secret)`` writes it and
``getSignedCookie(c, secret, "session")`` reads it back, ``False`` when the signature does not
match, ``None`` when the cookie is not there. The format is the one of takibi (npm), so the two
read each other's cookies with the same secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any, Literal, TypedDict, overload
from urllib.parse import quote, unquote

from .context import Context

Cookie = dict[str, str]
SignedCookie = dict[str, "str | Literal[False]"]
CookiePrefix = Literal["host", "secure"]
Secret = str | bytes


class CookieOptions(TypedDict, total=False):
    """The attributes of a ``Set-Cookie`` header, and ``prefix``."""

    domain: str
    expires: datetime  # naive datetimes are taken as UTC
    httpOnly: bool
    maxAge: int  # seconds
    path: str  # "/" unless given
    secure: bool
    sameSite: Literal["Strict", "Lax", "None", "strict", "lax", "none"]
    partitioned: bool
    priority: Literal["Low", "Medium", "High", "low", "medium", "high"]
    prefix: CookiePrefix


# A name a cookie is produced with (RFC 6265 token); parsing takes the names seen in the wild.
_VALID_NAME = re.compile(r"^[\w!#$%&'*.^`|~+-]+$", re.ASCII)
_RELAXED_NAME = re.compile(r"^[!#-:<>-\[\]-~]+$")
# ASCII 32-126 but `"`, `;` and `\` (space and comma allowed, as in the wild).
_VALID_VALUE = re.compile(r"^[ !#-:<-\[\]-~]*$")
_MAX_AGE = 34560000  # 400 days, the most a cookie should live


def _encode(value: str) -> str:
    """JavaScript's ``encodeURIComponent``."""
    return quote(value, safe="!*'()")


def parse(cookie: str, name: str | None = None) -> Cookie:
    """The cookies of a ``Cookie`` header value (``name``: that one only); the first of a name wins."""
    if name is not None and name not in cookie:
        return {}
    parsed: Cookie = {}
    for pair in cookie.split(";"):
        key, sep, value = pair.partition("=")
        if not sep:
            continue
        key = key.strip(" \t")
        if (name is not None and key != name) or not _RELAXED_NAME.match(key) or key in parsed:
            continue
        value = value.strip(" \t")
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if _VALID_VALUE.match(value):
            parsed[key] = unquote(value)
            if name is not None:
                break
    return parsed


def _key(secret: Secret) -> bytes:
    return secret.encode() if isinstance(secret, str) else bytes(secret)


def _sign(value: str, secret: Secret) -> str:
    """The base64 HMAC-SHA256 of ``value``: 44 characters, ending in ``=``."""
    return base64.b64encode(hmac.new(_key(secret), value.encode(), hashlib.sha256).digest()).decode()


def parseSigned(cookie: str, secret: Secret, name: str | None = None) -> SignedCookie:  # noqa: N802
    """The signed cookies of a ``Cookie`` header value: the value, or ``False`` when its signature is wrong."""
    parsed: SignedCookie = {}
    for key, value in parse(cookie, name).items():
        signed, sep, signature = value.rpartition(".")
        if not sep or len(signature) != 44 or not signature.endswith("="):
            continue
        parsed[key] = signed if hmac.compare_digest(_sign(signed, secret), signature) else False
    return parsed


def _http_date(when: datetime) -> str:
    aware = when.replace(tzinfo=UTC) if when.tzinfo is None else when
    return format_datetime(aware.astimezone(UTC), usegmt=True)


def _serialize(name: str, value: str, options: CookieOptions) -> str:
    if not _VALID_NAME.match(name):
        raise ValueError("Invalid cookie name")
    if name.startswith("__Secure-") and not options.get("secure"):
        raise ValueError("__Secure- Cookie must have Secure attributes")
    if name.startswith("__Host-"):
        if not options.get("secure"):
            raise ValueError("__Host- Cookie must have Secure attributes")
        if options.get("path") != "/":
            raise ValueError('__Host- Cookie must have Path attributes with "/"')
        if options.get("domain"):
            raise ValueError("__Host- Cookie must not have Domain attributes")
    for key in ("domain", "path", "sameSite", "priority"):
        attribute = options.get(key)
        if attribute and re.search(r"[;\r\n]", str(attribute)):
            raise ValueError(f'{key} must not contain ";", "\\r", or "\\n"')
    cookie = f"{name}={value}"
    max_age = options.get("maxAge")
    if max_age is not None and max_age >= 0:
        if max_age > _MAX_AGE:
            raise ValueError("Cookies Max-Age SHOULD NOT be greater than 400 days (34560000 seconds) in duration.")
        cookie += f"; Max-Age={int(max_age)}"
    domain = options.get("domain")
    if domain and options.get("prefix") != "host":
        cookie += f"; Domain={domain}"
    path = options.get("path")
    if path:
        cookie += f"; Path={path}"
    expires = options.get("expires")
    if expires is not None:
        now = datetime.now(UTC) if expires.tzinfo is not None else datetime.now(UTC).replace(tzinfo=None)
        if expires - now > timedelta(seconds=_MAX_AGE):
            raise ValueError("Cookies Expires SHOULD NOT be greater than 400 days (34560000 seconds) in the future.")
        cookie += f"; Expires={_http_date(expires)}"
    if options.get("httpOnly"):
        cookie += "; HttpOnly"
    if options.get("secure"):
        cookie += "; Secure"
    same_site = options.get("sameSite")
    if same_site:
        cookie += f"; SameSite={same_site[:1].upper()}{same_site[1:]}"
    priority = options.get("priority")
    if priority:
        cookie += f"; Priority={priority[:1].upper()}{priority[1:]}"
    if options.get("partitioned"):
        if not options.get("secure"):
            raise ValueError("Partitioned Cookie must have Secure attributes")
        cookie += "; Partitioned"
    return cookie


def serialize(name: str, value: str, options: CookieOptions | None = None) -> str:
    """A ``Set-Cookie`` header value, the cookie value URL-encoded."""
    return _serialize(name, _encode(value), options or {})


def serializeSigned(name: str, value: str, secret: Secret, options: CookieOptions | None = None) -> str:  # noqa: N802
    """A ``Set-Cookie`` header value for a signed cookie: ``value.signature``, URL-encoded."""
    return _serialize(name, _encode(f"{value}.{_sign(value, secret)}"), options or {})


def _prefixed(name: str, prefix: CookiePrefix | None) -> str:
    if prefix == "secure":
        return f"__Secure-{name}"
    if prefix == "host":
        return f"__Host-{name}"
    return name


@overload
def getCookie(c: Context[Any]) -> Cookie: ...
@overload
def getCookie(c: Context[Any], name: str, prefix: CookiePrefix | None = None) -> str | None: ...


def getCookie(c: Context[Any], name: str | None = None, prefix: CookiePrefix | None = None) -> Any:  # noqa: N802
    """A cookie of the request (``None`` when it is not there), or every cookie as a dict."""
    cookie = c.req.header("cookie")
    if name is None:
        return parse(cookie) if cookie else {}
    if not cookie:
        return None
    key = _prefixed(name, prefix)
    return parse(cookie, key).get(key)


@overload
def getSignedCookie(c: Context[Any], secret: Secret) -> SignedCookie: ...
@overload
def getSignedCookie(
    c: Context[Any], secret: Secret, name: str, prefix: CookiePrefix | None = None
) -> str | Literal[False] | None: ...


def getSignedCookie(  # noqa: N802
    c: Context[Any], secret: Secret, name: str | None = None, prefix: CookiePrefix | None = None
) -> Any:
    """A signed cookie's value: ``False`` when the signature does not match ``secret``, ``None``
    when the cookie is not there; or every signed cookie as a dict."""
    cookie = c.req.header("cookie")
    if name is None:
        return parseSigned(cookie, secret) if cookie else {}
    if not cookie:
        return None
    key = _prefixed(name, prefix)
    return parseSigned(cookie, secret, key).get(key)


def _with_prefix(name: str, options: CookieOptions | None) -> tuple[str, CookieOptions]:
    opts: CookieOptions = {"path": "/", **(options or {})}
    prefix = opts.get("prefix")
    if prefix == "secure":
        return f"__Secure-{name}", {**opts, "secure": True}
    if prefix == "host":
        opts = {**opts, "path": "/", "secure": True}
        opts.pop("domain", None)
        return f"__Host-{name}", opts
    return name, opts


def generateCookie(name: str, value: str, options: CookieOptions | None = None) -> str:  # noqa: N802
    """The ``Set-Cookie`` header value ``setCookie`` sends."""
    prefixed, opts = _with_prefix(name, options)
    return serialize(prefixed, value, opts)


def generateSignedCookie(name: str, value: str, secret: Secret, options: CookieOptions | None = None) -> str:  # noqa: N802
    """The ``Set-Cookie`` header value ``setSignedCookie`` sends."""
    prefixed, opts = _with_prefix(name, options)
    return serializeSigned(prefixed, value, secret, opts)


def setCookie(c: Context[Any], name: str, value: str, options: CookieOptions | None = None) -> None:  # noqa: N802
    """Adds a ``Set-Cookie`` header to the response the handler returns."""
    c.header("set-cookie", generateCookie(name, value, options), append=True)


def setSignedCookie(  # noqa: N802
    c: Context[Any], name: str, value: str, secret: Secret, options: CookieOptions | None = None
) -> None:
    """Adds a ``Set-Cookie`` header for a signed cookie to the response the handler returns."""
    c.header("set-cookie", generateSignedCookie(name, value, secret, options), append=True)


def deleteCookie(c: Context[Any], name: str, options: CookieOptions | None = None) -> str | None:  # noqa: N802
    """Expires the cookie (``Max-Age=0``) and returns the value the request carried, if any."""
    deleted = getCookie(c, name, (options or {}).get("prefix"))
    setCookie(c, name, "", {**(options or {}), "maxAge": 0})
    return deleted


__all__ = [
    "Cookie",
    "CookieOptions",
    "CookiePrefix",
    "SignedCookie",
    "deleteCookie",
    "generateCookie",
    "generateSignedCookie",
    "getCookie",
    "getSignedCookie",
    "parse",
    "parseSigned",
    "serialize",
    "serializeSigned",
    "setCookie",
    "setSignedCookie",
]
