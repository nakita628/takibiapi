from __future__ import annotations

import datetime as dt
import enum
import json
import sys
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, NotRequired, TypedDict

import pytest

from takibiapi import schema
from takibiapi.schema import Codec, SchemaBuilder, ValidationError


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class Tag:
    name: str


@dataclass
class Item:
    id: int
    title: Annotated[str, {"minLength": 1, "maxLength": 5}]
    price: float
    tags: list[Tag] = field(default_factory=list)
    color: Color = Color.RED
    note: str | None = None


class Filter(TypedDict):
    q: str
    limit: NotRequired[Annotated[int, {"minimum": 1, "maximum": 100}]]


@dataclass
class Node:
    value: int
    children: list[Node] = field(default_factory=list)


class TreeDict(TypedDict):
    value: int
    children: list[TreeDict]


class WithDate(TypedDict):
    when: dt.date


def test_primitives() -> None:
    b = SchemaBuilder()
    assert b.of(str) == {"type": "string"}
    assert b.of(int) == {"type": "integer"}
    assert b.of(float) == {"type": "number"}
    assert b.of(bool) == {"type": "boolean"}
    assert b.of(dt.datetime) == {"type": "string", "format": "date-time"}
    assert b.of(dt.date) == {"type": "string", "format": "date"}
    assert b.of(uuid.UUID) == {"type": "string", "format": "uuid"}
    assert b.of(Any) == {}
    assert b.of(list[int]) == {"type": "array", "items": {"type": "integer"}}
    assert b.of(tuple[str, ...]) == {"type": "array", "items": {"type": "string"}}
    assert b.of(set[str]) == {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
    assert b.of(dict[str, int]) == {"type": "object", "additionalProperties": {"type": "integer"}}
    assert b.of(Literal["a", "b"]) == {"type": "string", "enum": ["a", "b"]}
    assert b.of(Annotated[int, {"minimum": 0, "example": 3}]) == {"type": "integer", "minimum": 0, "example": 3}


def test_nullable_30_and_31() -> None:
    assert SchemaBuilder().of(int | None) == {"type": "integer", "nullable": True}
    assert SchemaBuilder(openapi31=True).of(int | None) == {"type": ["integer", "null"]}
    assert SchemaBuilder().of(Tag | None) == {"allOf": [{"$ref": "#/components/schemas/Tag"}], "nullable": True}
    assert SchemaBuilder(openapi31=True).of(Tag | None) == {
        "anyOf": [{"$ref": "#/components/schemas/Tag"}, {"type": "null"}]
    }
    assert SchemaBuilder().of(int | str) == {"anyOf": [{"type": "integer"}, {"type": "string"}]}


def test_named_types_become_components() -> None:
    b = SchemaBuilder()
    assert b.of(Item) == {"$ref": "#/components/schemas/Item"}
    assert b.components["Item"] == {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "title": {"type": "string", "minLength": 1, "maxLength": 5},
            "price": {"type": "number"},
            "tags": {"type": "array", "items": {"$ref": "#/components/schemas/Tag"}},
            "color": {"$ref": "#/components/schemas/Color", "default": "red"},
            "note": {"type": "string", "nullable": True, "default": None},
        },
        "required": ["id", "title", "price"],
    }
    assert b.components["Color"] == {"type": "string", "enum": ["red", "blue"]}
    assert b.components["Tag"] == {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}


def test_ref_with_keywords_in_30_and_31() -> None:
    assert SchemaBuilder().of(Annotated[Tag, {"description": "d"}]) == {
        "allOf": [{"$ref": "#/components/schemas/Tag"}],
        "description": "d",
    }
    assert SchemaBuilder(openapi31=True).of(Annotated[Tag, {"description": "d"}]) == {
        "$ref": "#/components/schemas/Tag",
        "description": "d",
    }


def test_typeddict_required_keys() -> None:
    b = SchemaBuilder()
    b.of(Filter)
    assert b.components["Filter"]["required"] == ["q"]


def test_recursive_types() -> None:
    b = SchemaBuilder()
    b.of(Node)
    b.of(TreeDict)
    assert b.components["Node"]["properties"]["children"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/Node"},
    }
    assert b.components["TreeDict"]["properties"]["children"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/TreeDict"},
    }


def test_same_name_twice_is_an_error() -> None:
    def make() -> type:
        @dataclass
        class Tag:  # another class named Tag
            other: int

        return Tag

    b = SchemaBuilder()
    b.of(Tag)
    with pytest.raises(TypeError, match="two schemas named 'Tag'"):
        b.of(make())


def test_unsupported_type() -> None:
    with pytest.raises(TypeError):
        SchemaBuilder().of(bytes)


# -- type checking (both engines) ---------------------------------------------------------- #
@pytest.fixture(params=["rust", "python"])
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "rust" and schema._core is None:
        pytest.skip("the Rust engine (takibiapi._core) is not built")
    if request.param == "python":
        monkeypatch.setattr(schema, "_core", None)
    return request.param


def _as_json(value: Any) -> bytes | None:
    try:
        return json.dumps(value).encode()
    except TypeError:
        return None


def decode(tp: Any, value: Any, *, coerce: bool = False) -> Any:
    """``Codec(tp).decode``; JSON values are also read from JSON bytes, with the same result."""
    codec = Codec(tp)
    out = codec.decode(value, coerce=coerce)
    data = None if coerce else _as_json(value)
    if data is not None:
        assert codec.decode_json(data) == out
    assert schema.decode(tp, value, coerce=coerce) == out  # the Python reference agrees
    return out


def issues(tp: Any, value: Any, **kwargs: Any) -> list[tuple[str, str]]:
    codec = Codec(tp)
    with pytest.raises(ValidationError) as info:
        codec.decode(value, **kwargs)
    found = info.value.issues
    data = None if kwargs.get("coerce") else _as_json(value)
    if data is not None:  # the one-pass JSON reader reports the same issues
        with pytest.raises(ValidationError) as from_json:
            codec.decode_json(data)
        assert from_json.value.issues == found
    with pytest.raises(ValidationError) as reference:  # and so does the Python reference
        schema.decode(tp, value, **kwargs)
    assert reference.value.issues == found
    return [(".".join(map(str, i["path"])), i["code"]) for i in found]


@pytest.mark.usefixtures("engine")
class TestTypeChecking:
    def test_builds_dataclasses(self) -> None:
        item = decode(Item, {"id": 1, "title": "milk", "price": 2, "tags": [{"name": "x"}], "color": "blue"})
        assert item == Item(1, "milk", 2.0, [Tag("x")], Color.BLUE)
        assert isinstance(item.price, float)

    def test_typeddict_drops_unknown_keys(self) -> None:
        assert decode(Filter, {"q": "a", "extra": 1}) == {"q": "a"}

    def test_reports_every_issue(self) -> None:
        assert issues(Item, {"id": True, "title": 3, "tags": [{}], "color": "green"}) == [
            ("id", "invalid_type"),
            ("title", "invalid_type"),
            ("price", "missing"),
            ("tags.0.name", "missing"),
            ("color", "invalid_value"),
        ]

    def test_annotated_keywords_are_not_checked(self) -> None:
        assert decode(Item, {"id": 1, "title": "far too long", "price": 1})  # maxLength: 5
        assert decode(Filter, {"q": "a", "limit": 1000}) == {"q": "a", "limit": 1000}  # maximum: 100
        assert decode(Annotated[str, {"pattern": "^[a-z]+$"}], "A1") == "A1"

    def test_text_with_coerce(self) -> None:
        assert decode(int, "42", coerce=True) == 42
        assert decode(float, "1.5", coerce=True) == 1.5
        assert decode(bool, "true", coerce=True) is True
        assert decode(Literal[1, 2], "2", coerce=True) == 2
        assert decode(Color, "red", coerce=True) is Color.RED
        assert decode(int, "123456789012345678901234567890", coerce=True) == 123456789012345678901234567890
        for text in ("", "1.5", "\uff11", " 1", "1_000"):  # \uff11: a full-width "1"
            assert issues(int, text, coerce=True) == [("", "invalid_type")]
        for text in ("nan", "inf", "1e", "0x1"):
            assert issues(float, text, coerce=True) == [("", "invalid_type")]
        assert issues(bool, "True", coerce=True) == [("", "invalid_type")]
        assert issues(int, "42") == [("", "invalid_type")]  # JSON: no conversion

    def test_formats(self) -> None:
        assert decode(dt.datetime, "2026-01-02T03:04:05+00:00") == dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)
        assert decode(dt.date, "2026-01-02") == dt.date(2026, 1, 2)
        assert issues(dt.date, "2026-01-02T00:00:00") == [("", "invalid_format")]
        assert issues(dt.datetime, "yesterday") == [("", "invalid_format")]
        assert decode(uuid.UUID, "12345678-1234-5678-1234-567812345678") == uuid.UUID(
            int=0x12345678123456781234567812345678
        )

    def test_optional_and_union(self) -> None:
        assert decode(int | None, None) is None
        assert issues(int | None, "x") == [("", "invalid_type")]
        assert decode(int | str, "x") == "x"
        assert issues(int | bool, "x") == [("", "invalid_union")]

    def test_more_shapes(self) -> None:
        assert decode(dict[str, list[int]], {"a": [1, 2], "b": []}) == {"a": [1, 2], "b": []}
        assert decode(tuple[int, ...], [1, 2]) == (1, 2)
        assert decode(set[str], ["a", "a"]) == {"a"}
        assert decode(Node, {"value": 1, "children": [{"value": 2}]}) == Node(1, [Node(2)])
        assert decode(TreeDict, {"value": 1, "children": []}) == {"value": 1, "children": []}
        assert decode(Literal[1, "1", True], True) is True
        assert decode(int, 3.0) == 3
        assert decode(Any, {"x": [1]}) == {"x": [1]}
        assert issues(Literal[1], True) == [("", "invalid_value")]
        assert issues(Literal[1], 1.0) == [("", "invalid_value")]
        assert issues(dict[str, int], {"a": "x"}) == [("a", "invalid_type")]
        assert issues(list[int], [1, "x", None]) == [("1", "invalid_type"), ("2", "invalid_type")]
        assert issues(Node, {"value": 1, "children": [{"value": "x"}]}) == [("children.0.value", "invalid_type")]
        assert issues(Item, None) == [("", "invalid_type")]

    def test_dataclass_construction(self) -> None:
        @dataclass(frozen=True)
        class Frozen:
            a: int
            b: list[int] = field(default_factory=list)

        @dataclass
        class WithPostInit:
            a: int

            def __post_init__(self) -> None:
                self.a *= 2

        assert decode(Frozen, {"a": 1}) == Frozen(1, [])
        assert decode(WithPostInit, {"a": 2}).a == 4  # __post_init__ ran
        assert schema._direct(Frozen)
        assert not schema._direct(WithPostInit)

    def test_malformed_json(self) -> None:
        for data in (b"", b"{", b'{"id": 1,}'):
            with pytest.raises(ValueError, match=r"Malformed JSON|Expecting|Extra data|Illegal"):
                Codec(Item).decode_json(data)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
