# takibi-swagger-ui

Swagger UI for a [TakibiAPI](../takibiapi) app (or its [pydantic](../pydantic-takibiapi) / [zopy](../zopy-takibiapi) flavors), as Hono's [`@hono/swagger-ui`](https://github.com/honojs/middleware/tree/main/packages/swagger-ui).

```sh
pip install takibi-swagger-ui
```

```python
from takibiapi import TakibiAPI
from takibi_swagger_ui import swaggerUI

app = TakibiAPI()
# app.openapi(route, handler) ...
app.doc("/doc", {"openapi": "3.1.0", "info": {"title": "My API", "version": "1.0.0"}})
app.use(swaggerUI({"url": "/doc"}), prefix="/ui")  # GET /ui: the Swagger UI page
```

The page loads `swagger-ui-dist` from jsDelivr (`"version": "5.17.14"` pins it) and the document from `url`.

## Options

- `title`: the page's `<title>` (default `SwaggerUI`).
- `version`: the `swagger-ui-dist` version.
- `manuallySwaggerUIHtml`: `(asset) -> str`, the page body built from `asset["css"]` / `asset["js"]`.
- Everything else is [Swagger UI's configuration](https://swagger.io/docs/open-source-tools/swagger-ui/usage/configuration/) (`url`, `urls`, `spec`, `deepLinking`, `docExpansion`, `persistAuthorization`, ...), written as JSON. `presets`, `plugins`, `requestInterceptor`, `responseInterceptor`, `onComplete`, `operationsSorter`, `tagsSorter`, `modelPropertyMacro` and `parameterMacro` are JavaScript source and written as they are:

```python
swaggerUI({"url": "/doc", "requestInterceptor": "(req) => { req.headers['x-api-key'] = 'dev'; return req }"})
```

`SwaggerUI(options)` returns the markup alone, to put in a page of your own.

## Development

From the repository root: `uv run pytest packages/takibi-swagger-ui`. See the root README.

## License

Distributed under the MIT License. See [LICENSE](https://github.com/nakita628/takibiapi/blob/main/packages/takibi-swagger-ui/LICENSE) for more information.
