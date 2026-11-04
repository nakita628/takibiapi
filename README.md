# TakibiAPI

REST APIs for Python whose OpenAPI document, request checks and handler types come from one route declaration.

| Package | |
| --- | --- |
| [`takibiapi`](packages/takibiapi) | The framework: Python's own types as schemas, with a Rust engine. |
| [`pydantic-takibiapi`](packages/pydantic-takibiapi) | With pydantic models as schemas. |
| [`zopy-takibiapi`](packages/zopy-takibiapi) | With [zopy](https://github.com/nakita628/zopy) schemas. |
| [`takibi-swagger-ui`](packages/takibi-swagger-ui) | Swagger UI middleware. |

## Development

```sh
uv sync            # install every package, building the Rust extension
uv run just check  # lint, type-check and test, as CI does
```

To release a package, bump its version and add a `## <version>` entry to its `CHANGELOG.md`, then push to `main`.

## License

[MIT](LICENSE)
