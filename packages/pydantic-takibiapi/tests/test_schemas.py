from __future__ import annotations

import sys

import pytest

from pydantic_takibiapi.schemas import to_openapi30


def test_nullable() -> None:
    assert to_openapi30({"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}) == {
        "type": "string",
        "default": None,
        "nullable": True,
    }
    assert to_openapi30({"anyOf": [{"$ref": "#/x"}, {"type": "null"}]}) == {
        "allOf": [{"$ref": "#/x"}],
        "nullable": True,
    }
    assert to_openapi30({"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}) == {
        "anyOf": [{"type": "string"}, {"type": "integer"}],
        "nullable": True,
    }
    assert to_openapi30({"type": ["string", "null"]}) == {"type": "string", "nullable": True}


def test_keywords() -> None:
    assert to_openapi30({"const": "a"}) == {"enum": ["a"]}
    assert to_openapi30({"examples": [1, 2]}) == {"example": 1}
    assert to_openapi30({"exclusiveMinimum": 0, "exclusiveMaximum": 9}) == {
        "minimum": 0,
        "exclusiveMinimum": True,
        "maximum": 9,
        "exclusiveMaximum": True,
    }
    assert to_openapi30({"$ref": "#/x", "description": "d"}) == {"description": "d", "allOf": [{"$ref": "#/x"}]}


def test_nested() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "array", "items": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}},
    }
    assert to_openapi30(schema)["properties"]["a"]["items"] == {"type": "integer", "nullable": True}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
