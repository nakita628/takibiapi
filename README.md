# TakibiAPI

The Python packages of [Takibi](https://github.com/nakita628/takibi): a route is declared once with `createRoute`, its handler is declared apart for that route, and `app.openapi(route, handler)` registers the two. The request checks, the handler's types and the OpenAPI document come from that one declaration.

## Packages

| Package | What it is |
| --- | --- |
| [`takibiapi`](packages/takibiapi) | The framework. Schemas are Python's own types; JSON, routing and type checks run in a Rust extension. No runtime dependencies. ASGI, and Granian's RSGI. |
| [`pydantic-takibiapi`](packages/pydantic-takibiapi) | The same, with strict pydantic models as schemas: constraints, unknown keys and responses are checked. |
| [`zopy-takibiapi`](packages/zopy-takibiapi) | The same, with [zopy](https://github.com/nakita628/zopy) schemas. |
| [`takibi-swagger-ui`](packages/takibi-swagger-ui) | Swagger UI middleware for any of the above. |

Each package's README has its quick start and the details.

## Development

One [uv](https://docs.astral.sh/uv/) workspace: every package lives under `packages/`, and `uv sync` installs them all editable into `.venv`, building the Rust extension of `takibiapi` with maturin (the crate is the one member of the Cargo workspace in `Cargo.toml`). `uv run just` lists the recipes:

- `just test` runs pytest over every package (`just test packages/takibiapi` for one), `just test-rust` the Rust unit tests.
- `just develop` rebuilds the extension after a change under `packages/takibiapi/src`.
- `just lint`, `just typecheck` and `just fmt`; `just check` runs everything CI runs.
- `just dist <package>` builds a package's wheel and sdist into `dist/`.

## Releasing

Each package is released on its own. Bump its version in `pyproject.toml`, add a `## <version>` entry to its `CHANGELOG.md`, and push to `main`: once CI is green, `release.yml` publishes every package whose version is not on PyPI yet, tagged `<directory>-v<version>`, with trusted publishing. A manual run publishes to TestPyPI. The trusted publishers (PyPI, TestPyPI) are registered for the `release.yml` workflow and the `pypi` and `testpypi` environments.

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
