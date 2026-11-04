# pydantic-takibiapi

[TakibiAPI](../takibiapi) with [pydantic](https://pydantic.dev/) models as the schemas, checked strictly: constraints, unknown keys and responses included.

```sh
pip install pydantic-takibiapi
```

## Quick start

```python
from pydantic_takibiapi import Context, Model, TakibiAPI, createRoute


class Message(Model):
    message: str


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
    return c.json(Message(message="takibi🔥"))


app = TakibiAPI()
app.openapi(getTakibiRoute, getTakibiRouteHandler)
app.doc("/doc", {"openapi": "3.1.0", "info": {"title": "takibi API", "version": "1.0.0"}})
```

```bash
granian --interface rsgi myapp:app   # or: uvicorn myapp:app
curl localhost:8000/takibi           # {"message":"takibi🔥"}
```

Import `TakibiAPI` from `pydantic_takibiapi`; everything else works as in Takibi.

## Models

Every model, nested ones included, must be strict: subclass `Model` (a `BaseModel` with `strict=True, extra="forbid"`). `app.openapi` raises `TypeError` otherwise. `Field` constraints, aliases and validators work as in pydantic.

## Checks

- **Requests**: `c.req.valid(...)` gives a checked model instance. Path, query, header and cookie text is converted (`"7"` → `7`); JSON bodies are strict (`"1"` is not an `int`) and reject unknown keys. A failure gets **422** with pydantic's error types as `code`.
- **Responses**: `c.json(value, status)` checks `value` against the model declared for that status. A mismatch raises `ResponseValidationError` and answers **500** (details in the log, not in the response).

```python
app.onError(lambda c: c.json({"error": str(c.error)}, 500))  # replaces the 500, ResponseValidationError included
```

## OpenAPI

Schemas come from pydantic's JSON Schema; every model is a component (`Name-Input` / `Name-Output` when reading and writing differ). `"3.0.x"` documents are converted from it.

## Development

From the repository root: `uv run pytest packages/pydantic-takibiapi`. See the root README.

## License

Distributed under the MIT License. See [LICENSE](https://github.com/nakita628/takibiapi/blob/main/packages/pydantic-takibiapi/LICENSE) for more information.
