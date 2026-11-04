# zopy-takibiapi

## 0.2.0

### Minor Changes

- `OpenAPIObject`: name an object for the OpenAPI document in its declaration, `class Todo(OpenAPIObject, openapi="Todo")`, as `z.object({...}).openapi("Todo")` in `@hono/zod-openapi`. The class itself is the component, so one name is the route's schema and the type the handler returns
- Move to the takibiapi repository (https://github.com/nakita628/takibiapi); the project URLs point there

## 0.1.0

### Minor Changes

- Initial release
