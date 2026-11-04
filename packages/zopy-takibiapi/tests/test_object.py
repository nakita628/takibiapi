"""``OpenAPIObject``: the OpenAPI name and metadata in the class declaration."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from zopy_takibiapi import Context, OpenAPIObject, TakibiAPI, createRoute, z

CONFIG: Any = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}}


class Tag(OpenAPIObject, openapi={"id": "Tag", "description": "A label"}):
    name = z.string()


class Item(OpenAPIObject, openapi="Item"):
    id = z.int().openapi({"example": 1})
    tags = z.array(Tag)


class Described(OpenAPIObject, openapi={"description": "Not named: written in place"}):
    note = z.string()


class Plain(OpenAPIObject):  # no openapi=: a strict object written in place
    x = z.int()


class Child(Item):  # a subclass is not named after its base
    extra = z.string()


def _app(response: Any, *, path: str = "/items") -> TakibiAPI:
    class getItemsRoute(
        createRoute(
            {
                "method": "get",
                "path": path,
                "responses": {200: {"description": "ok", "content": {"application/json": {"schema": response}}}},
            }
        )
    ):
        pass

    def getItemsRouteHandler(c: Context[Any]):
        return c.json(Item(id=1, tags=[Tag(name="a")]), 200)

    getItemsRouteHandler.__annotations__["c"] = Context[getItemsRoute]
    app = TakibiAPI()
    app.openapi(getItemsRoute, getItemsRouteHandler)
    return app


def _schema(doc: dict[str, Any], path: str = "/items") -> Any:
    return doc["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]


def test_the_class_itself_is_the_component() -> None:
    doc = _app(Item).getOpenAPIDocument(CONFIG)
    assert _schema(doc) == {"$ref": "#/components/schemas/Item"}
    schemas = doc["components"]["schemas"]
    assert schemas["Item"]["properties"] == {
        "id": {"type": "integer", "example": 1},
        "tags": {"type": "array", "items": {"$ref": "#/components/schemas/Tag"}},
    }
    assert schemas["Item"]["additionalProperties"] is False
    assert schemas["Tag"]["description"] == "A label"


@pytest.mark.parametrize("schema", [Described, Plain, Child])
def test_unnamed_objects_are_written_in_place(schema: Any) -> None:
    doc = _app(schema).getOpenAPIDocument(CONFIG)
    written = _schema(doc)
    assert "$ref" not in written
    assert written["type"] == "object"
    assert written["additionalProperties"] is False
    if schema is Described:
        assert written["description"] == "Not named: written in place"


def test_the_named_class_checks_and_encodes_responses() -> None:
    app = _app(Item)

    async def call() -> tuple[int, bytes]:
        messages: list[Any] = []

        async def receive() -> Any:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Any) -> None:
            messages.append(message)

        scope = {"type": "http", "method": "GET", "path": "/items", "query_string": b"", "headers": []}
        await app(scope, receive, send)
        return messages[0]["status"], b"".join(m.get("body", b"") for m in messages[1:])

    assert asyncio.run(call()) == (200, b'{"id":1,"tags":[{"name":"a"}]}')


def test_objects_are_strict() -> None:
    result = Item.safeParse({"id": 1, "tags": [], "other": True})
    assert not result.success
    assert [issue["code"] for issue in result.error.issues] == ["unrecognized_keys"]


@pytest.mark.parametrize(
    ("openapi", "message"),
    [
        ("", "must name the component"),
        (1, "a component name or a metadata mapping"),
        ({"param": {"in": "query"}}, "cannot take 'param'"),
    ],
)
def test_bad_openapi_arguments(openapi: Any, message: str) -> None:
    with pytest.raises(TypeError, match=message):

        class Bad(OpenAPIObject, openapi=openapi):
            x = z.int()


def test_two_classes_with_one_name_are_refused() -> None:
    class Item(OpenAPIObject, openapi="Item"):  # another schema under the module-level Item's name
        other = z.string()

    app = _app(globals()["Item"])
    app.route("/v2", _app(Item))
    with pytest.raises(TypeError, match="two different schemas are named 'Item'"):
        app.getOpenAPIDocument(CONFIG)
