# zopy-takibiapi

[TakibiAPI](../takibiapi) with [zopy](https://github.com/nakita628/zopy) (Zod for Python) schemas, checked strictly: checks, unknown keys and responses included.

```sh
pip install zopy-takibiapi
```

## Quick start

```python
from zopy_takibiapi import Context, OpenAPIObject, TakibiAPI, createRoute, z


class Message(OpenAPIObject, openapi="Message"):  # components.schemas.Message, referenced with $ref
    message = z.string().openapi({"example": "takibi🔥"})


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
    return c.json(Message(message="takibi🔥"), 200)


app = TakibiAPI()
app.openapi(getTakibiRoute, getTakibiRouteHandler)
app.doc("/doc", {"openapi": "3.0.0", "info": {"title": "takibi API", "version": "1.0.0"}})
```

```bash
granian --interface rsgi myapp:app   # or: uvicorn myapp:app
curl localhost:8000/takibi           # {"message":"takibi🔥"}
```

Import `TakibiAPI`, `OpenAPIObject` and `z` from `zopy_takibiapi`; everything else works as in Takibi.

## Schemas

Every object, nested ones included, must be `z.StrictObject` (or `OpenAPIObject`, which is one); `app.openapi` raises `TypeError` for a loose object or a non-zopy schema. Parameters arrive as text and are parsed as Zod parses them, so convert explicitly: `todoId = z.coerce.number().int()`.

## Checks

- **Requests**: `c.req.valid(...)` gives the parsed value. A failure gets **422** with Zod's issue codes as `code`.
- **Responses**: `c.json(value, status)` encodes `value` with the schema declared for that status (`safeEncode`: checked, codecs to their wire form). A mismatch raises `ResponseValidationError` and answers **500** (details in the log, not in the response).

```python
app.onError(lambda c: c.json({"error": str(c.error)}, 500))  # replaces the 500, ResponseValidationError included
```

## OpenAPI

The OpenAPI metadata is written with the schema, as in `@hono/zod-openapi`:

```python
class Todo(OpenAPIObject, openapi="Todo"):  # z.object({...}).openapi("Todo")
    id = z.int().positive().openapi({"example": 1})
    title = z.string().openapi({"description": "What to do"})


class Tag(OpenAPIObject, openapi={"id": "Tag", "description": "A label"}):  # a name and metadata
    name = z.string()
```

`OpenAPIObject` is a `z.StrictObject` whose class keyword `openapi=` names it: the class itself becomes `components.schemas.Todo`, referenced with `$ref` wherever it is used, so one name serves as the route's schema and as the type the handler returns (`c.json(Todo(...))`). A mapping without `"id"` adds metadata only, and a subclass is unnamed until it says otherwise. zopy's `Todo.openapi("Todo")` still works, but names a copy of the class.

As in Zod, only named schemas become components; others are written in place. Documents come from zopy's `toJSONSchema` (`openapi-3.0` or JSON Schema 2020-12).

## Development

From the repository root: `uv run pytest packages/zopy-takibiapi`. See the root README.

## License

Distributed under the MIT License. See [LICENSE](https://github.com/nakita628/takibiapi/blob/main/packages/zopy-takibiapi/LICENSE) for more information.
