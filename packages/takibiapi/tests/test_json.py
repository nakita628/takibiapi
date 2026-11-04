"""The Rust JSON reader / writer and query parser (takibiapi._core) give exactly what Python's own
``json`` and ``urllib.parse`` give."""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import json
import math
import os
import random
import struct
import sys
import uuid
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qsl

import pytest

from takibiapi import schema

core = schema._core
needs_core = pytest.mark.skipif(core is None, reason="the Rust engine (takibiapi._core) is not built")


def test_engine_is_built_in_ci() -> None:
    # CI installs the package with its extension (uv sync builds it), so these tests must not be skipped there.
    if os.environ.get("CI"):
        assert core is not None


def py_dumps(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=schema.to_json).encode()


def py_query(qs: bytes) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, v in parse_qsl(qs.decode("latin-1"), keep_blank_values=True):
        out.setdefault(k, []).append(v)
    return out


class Color(enum.Enum):
    RED = "red"


class Level(enum.IntEnum):
    HIGH = 3


@dataclasses.dataclass
class Point:
    x: int
    y: float


FLOATS = [
    0.0,
    -0.0,
    1.0,
    -1.5,
    0.1,
    1e16,
    1e15,
    123456789012345680.0,
    1e-4,
    1e-5,
    1.5e-7,
    5e-324,
    1.7976931348623157e308,
    2.5,
    100.0,
    1e22,
    1 / 3,
]


@needs_core
@pytest.mark.parametrize("value", FLOATS)
def test_float_repr(value: float) -> None:
    assert core.dumps(value) == py_dumps(value)


@needs_core
def test_random_floats() -> None:
    rng = random.Random(0)
    for _ in range(20000):
        value = struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))[0]
        if math.isfinite(value):
            assert core.dumps(value) == py_dumps(value), value
    for _ in range(5000):
        value = rng.uniform(-1e6, 1e6) if rng.random() < 0.5 else rng.random() * 10 ** rng.randint(-30, 30)
        assert core.dumps(value) == py_dumps(value), value


@needs_core
def test_dumps_values() -> None:
    values: list[Any] = [
        None,
        True,
        [1, -2, 10**30, -(10**25)],
        {"a": {"b": [1, "x", None]}, "": []},
        'quote " backslash \\ / \b\f\n\r\t \x00 \x1f \x7f é 😀 \u2028',
        (1, 2),
        OrderedDict(a=1),
        Color.RED,
        Level.HIGH,
        Point(1, 2.0),
        [dt.datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=dt.UTC), dt.date(2026, 1, 2), dt.time(1, 2)],
        uuid.UUID(int=1),
        {"set": {1}},
    ]
    for value in values:
        assert core.dumps(value) == py_dumps(value), value


class Opaque:
    def __init__(self, value: Any) -> None:
        self.value = value


def _opaque(o: Any) -> Any:
    if isinstance(o, Opaque):
        return {"opaque": o.value}
    raise TypeError(type(o).__name__)


@needs_core
def test_dumps_default() -> None:
    values: list[Any] = [Opaque(1), [Opaque(Opaque("x")), Point(1, 2.0)], {"a": Opaque(None), "c": Color.RED}]
    for value in values:
        expected = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda o: schema.to_json(o) if schema.to_json(o) is not o else _opaque(o),
        ).encode()
        assert core.dumps(value, _opaque) == expected
        assert schema.dumps(value, default=_opaque) == expected
    with pytest.raises(TypeError):
        core.dumps(object(), _opaque)
    assert core.dumps(Opaque(1), lambda o: o) is None  # a default returning its input: left to json


@pytest.mark.parametrize("rust", [True, False])
def test_dumps_lone_surrogates(rust: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    # UTF-8 cannot carry them, so the JSON escapes them (json.dumps' default form), for any engine.
    if not rust:
        monkeypatch.setattr(schema, "_core", None)
    value = {"title": "\ud800 é", "nested": [Opaque("\udfff")]}
    expected = json.dumps(value, separators=(",", ":"), default=_opaque).encode()
    assert schema.dumps(value, default=_opaque) == expected
    assert json.loads(schema.dumps({"t": "\ud800"})) == {"t": "\ud800"}


def test_dumps_default_without_rust(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schema, "_core", None)
    assert schema.dumps([Opaque(1), Color.RED], default=_opaque) == b'[{"opaque":1},"red"]'


@needs_core
def test_dumps_leaves_the_rest_to_python() -> None:
    for value in (float("nan"), {1: "int key"}, "\ud800", object(), {"x": float("inf")}):
        assert core.dumps(value) is None
    loop: list[Any] = []
    loop.append(loop)
    assert core.dumps(loop) is None


DOCUMENTS = [
    b'{"a": 1, "b": [true, false, null], "c": {"d": "e"}}',
    b"  [ 1 , 2.5 , -0 , -0.0 , 1e3 , 1E-3 , 1.5e+2 ]  ",
    b"123456789012345678901234567890",
    b"-123456789012345678901234567890",
    b"1e400",
    b'"\\u00e9\\ud83d\\ude00 \\" \\\\ \\/ \\b \\f \\n \\r \\t"',
    '"é 😀 日本"'.encode(),
    b'{"dup": 1, "dup": 2}',
    b"[]",
    b"{}",
    b'""',
    b"0",
    b"null",
]


@needs_core
@pytest.mark.parametrize("data", DOCUMENTS)
def test_loads(data: bytes) -> None:
    out = core.loads(data)
    assert out is not None
    assert json.dumps(out[0]) == json.dumps(json.loads(data))


MALFORMED = [
    b"",
    b"{",
    b"[1,]",
    b"[1 2]",
    b'{"a" 1}',
    b"{'a': 1}",
    b"01",
    b"1.",
    b".5",
    b"-",
    b"+1",
    b"tru",
    b'"a',
    b'"\x01"',
    b'"\\x"',
    b"[1]x",
    b'"\\u12"',
    b"\xff",
    b'"\xc3"',
]


@needs_core
@pytest.mark.parametrize("data", MALFORMED)
def test_loads_malformed(data: bytes) -> None:
    with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
        json.loads(data)
    with pytest.raises(ValueError, match="Malformed JSON"):
        core.loads(data)


@needs_core
@pytest.mark.parametrize(
    "data",
    [b"NaN", b"[Infinity]", b"-Infinity", b'"\\ud800"', b'"\\udc00x"', b"\xef\xbb\xbf{}", '{"a": 1}'.encode("utf-16")],
)
def test_loads_leaves_the_rest_to_python(data: bytes) -> None:
    assert core.loads(data) is None
    expected = json.loads(data)
    assert schema.loads(data) == expected or math.isnan(expected)


@needs_core
def test_random_round_trip() -> None:
    rng = random.Random(1)
    alphabet = 'ab é😀"\\\n\t\x01/日'

    def value(depth: int) -> Any:
        kind = rng.randrange(8 if depth < 4 else 5)
        if kind == 0:
            return None
        if kind == 1:
            return rng.random() < 0.5
        if kind == 2:
            return rng.randint(-(10**20), 10**20)
        if kind == 3:
            return rng.uniform(-1e10, 1e10)
        if kind == 4:
            return "".join(rng.choice(alphabet) for _ in range(rng.randrange(8)))
        if kind in (5, 6):
            return [value(depth + 1) for _ in range(rng.randrange(4))]
        return {"".join(rng.choice(alphabet) for _ in range(3)): value(depth + 1) for _ in range(rng.randrange(4))}

    for _ in range(2000):
        v = value(0)
        assert core.dumps(v) == py_dumps(v)
        assert core.loads(py_dumps(v)) == (json.loads(py_dumps(v)),)
        pretty = json.dumps(v, indent=2).encode()
        assert core.loads(pretty) == (json.loads(pretty),)


QUERIES = [
    b"",
    b"a=1",
    b"a=1&a=2&b=",
    b"a",
    b"&&a=1&",
    b"x=a+b%20c",
    b"k%3D=v%26",
    b"q=%E3%81%82",
    b"bad=%zz&p=%",
    b"a=1=2",
    b"=v",
]


@needs_core
@pytest.mark.parametrize("qs", QUERIES)
def test_parse_query(qs: bytes) -> None:
    assert core.parse_query(qs) == py_query(qs)


@needs_core
def test_parse_query_leaves_the_rest_to_python() -> None:
    assert core.parse_query("a=é".encode("latin-1")) is None  # non-ASCII bytes
    assert core.parse_query(b"a=%ff") is None  # invalid UTF-8 (Python writes U+FFFD)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
