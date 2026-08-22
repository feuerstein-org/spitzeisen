# Generated weather SDK example

This directory is a miniature downstream SDK repository. It keeps vendor inputs, handwritten
extension points, and committed generated output separate in the same way a real package would.
The vendor input transcribes OpenWeather's official
[Current Weather documentation](https://openweathermap.org/api/current?collection=current_forecast)
into OpenAPI 3.1 because the source page itself is HTML.

```text
spec/vendor.json                 OpenAPI transcription of the official request and response
spec/overlay.yaml                explicit requiredness and numeric schema constraints
spec/manifest.yaml               SDK semantics OpenAPI cannot express
weather_sdk/models/_generated.py regenerated schema-derived models
weather_sdk/models/_exports.py   regenerated public schema export map
weather_sdk/models/current_weather.py create-once public model with a custom validator
weather_sdk/*/_generated/*.py    regenerated endpoint and aggregate-client bases
weather_sdk/_async/*.py          create-once public async extension classes
weather_sdk/_sync/*.py           create-once public sync extension classes
weather_sdk/__init__.py          handwritten package exports
demo.py                          offline end-to-end usage through FakeRouter
```

From the repository root:

```bash
mise run codegen
mise run check-codegen
mise run demo-example
```

`codegen` validates the vendor input, applies the overlay temporarily, compiles endpoint policy,
generates models, and writes both endpoint surfaces in one transaction. No patched or pruned
intermediate is kept as a second source of truth.

Files named or nested under `_generated` are replaced on every run. Public model, endpoint, and
client modules are created only when absent, so adding custom SDK behaviour there is safe.
`models/current_weather.py` demonstrates this with a validator requiring a successful observation
to contain at least one weather condition. The check task verifies generated models, replaceable
endpoint/client bases, public exports, and the presence of every public extension module.

Generated schema classes inherit `SpitzeisenModel`: shared model policy stays centralized, aliases
remain wire-only, and no repeated `model_config` block is emitted into each class.
Every type in the public response graph is re-exported through `weather_sdk.models`, so callers can
write annotations such as `from weather_sdk.models import Wind` without importing private storage.
Root response names resolve to their user-owned subclasses; nested schema names resolve to the
exact generated classes used inside those responses.

The operation requires `lat`, `lon`, and the `appid` security credential, and optionally accepts
`units`, `lang`, or a non-JSON `mode`. The manifest exposes the coordinates as `latitude` and
`longitude`, renames `lang` to `language`, and excludes `mode` because Spitzeisen consumes JSON.
The OpenAPI security scheme keeps `appid` out of the method while `QueryParamAuth` supplies it at
runtime. This is the same division a production SDK would use: OpenAPI describes the wire, the
overlay makes schema assumptions explicit, the manifest shapes the Python API, and runtime config
owns credentials.
