# TakibiAPI

[![Sponsor](https://img.shields.io/github/sponsors/nakita628?label=Sponsor&logo=GitHub)](https://github.com/sponsors/nakita628)

REST APIs for Python whose OpenAPI document, request checks and handler types come from one route declaration.

| Package | |
| --- | --- |
| [`takibiapi`](packages/takibiapi) | The framework: Python's own types as schemas, with a Rust engine. |
| [`pydantic-takibiapi`](packages/pydantic-takibiapi) | With pydantic models as schemas. |
| [`zopy-takibiapi`](packages/zopy-takibiapi) | With [zopy](https://github.com/nakita628/zopy) schemas. |
| [`takibi-swagger-ui`](packages/takibi-swagger-ui) | Swagger UI middleware. |

## Quick start

```sh
pip install zopy-takibiapi
```

```python
from zopy_takibiapi import Context, OpenAPIObject, TakibiAPI, createRoute, z


class Message(OpenAPIObject, openapi="Message"):
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

```sh
granian --interface rsgi myapp:app   # or: uvicorn myapp:app
curl localhost:8000/takibi           # {"message":"takibi🔥"}
```

See [`zopy-takibiapi`](packages/zopy-takibiapi) for schemas, checks and the OpenAPI document.

## Development

```sh
uv sync            # install every package, building the Rust extension
uv run just check  # lint, type-check and test, as CI does
```

To release a package, bump its version and add a `## <version>` entry to its `CHANGELOG.md`, then push to `main`.

## Sponsor

If TakibiAPI helps you, please consider [sponsoring its development](https://github.com/sponsors/nakita628).

## License

[MIT](LICENSE)
