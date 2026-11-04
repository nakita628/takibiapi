# zopy-takibiapi

[TakibiAPI](../takibiapi) with [zopy](https://github.com/nakita628/zopy) (Zod for Python) schemas, checked strictly: checks, unknown keys and responses included.

```sh
pip install zopy-takibiapi
```

## Quick start

```python
from zopy_takibiapi import Context, TakibiAPI, createRoute, z


class Message(z.StrictObject):
    message = z.string()


MessageSchema = Message.openapi("Message")  # components.schemas.Message, referenced with $ref


class getTakibiRoute(
    createRoute(
        {
            "method": "get",
            "path": "/takibi",
            "responses": {200: {"description": "takibi🔥", "content": {"application/json": {"schema": MessageSchema}}}},
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

Import `TakibiAPI` and `z` from `zopy_takibiapi`; everything else works as in Takibi.

## Schemas

Every object, nested ones included, must be `z.StrictObject`; `app.openapi` raises `TypeError` for a loose object or a non-zopy schema. Parameters arrive as text and are parsed as Zod parses them, so convert explicitly: `todoId = z.coerce.number().int()`.

## Checks

- **Requests**: `c.req.valid(...)` gives the parsed value. A failure gets **422** with Zod's issue codes as `code`.
- **Responses**: `c.json(value, status)` encodes `value` with the schema declared for that status (`safeEncode`: checked, codecs to their wire form). A mismatch raises `ResponseValidationError` and answers **500** (details in the log, not in the response).

```python
app.onError(lambda c: c.json({"error": str(c.error)}, 500))  # replaces the 500, ResponseValidationError included
```

## OpenAPI

As in Zod, only `.openapi("Name")` schemas become components; others are written in place. Documents come from zopy's `toJSONSchema` (`openapi-3.0` or JSON Schema 2020-12).

## Development

From the repository root: `uv run pytest packages/zopy-takibiapi`. See the root README.

## License

Distributed under the MIT License. See [LICENSE](https://github.com/nakita628/takibiapi/blob/main/packages/zopy-takibiapi/LICENSE) for more information.
