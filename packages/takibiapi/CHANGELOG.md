# takibiapi

## 0.2.0

### Minor Changes

- Typed response statuses: `c.json`, `c.text` and `c.body` take only a status the route declares (200 when none is given; any status when the route declares `"default"`), and a handler registered with `app.openapi` must return what they build, a `TypedResponse`. A `Response` built by hand, or a status the route does not declare, is a type error
- **Breaking (types only)**: the statuses are inferred from the literal keys of `responses`, so a status passed as an `int` variable, or shared responses typed `dict[int, ResponseConfig[...]]`, are type errors; annotate them with their codes, `Mapping[Literal[400, 404], ResponseConfig[Problem]]`. `Route` takes a sixth type parameter, the statuses. Nothing changes at run time
- Export `StatusCode` and `TypedResponse`

## 0.1.1

### Patch Changes

- Move to the takibiapi repository (https://github.com/nakita628/takibiapi); the project URLs point there

## 0.1.0

### Minor Changes

- Initial release
