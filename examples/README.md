# Generated weather SDK example

This directory is a miniature downstream SDK repository. It keeps vendor inputs, handwritten
extension points, and committed generated output separate in the same way a real package would.
The vendor input transcribes OpenWeather's official
[Current Weather documentation](https://openweathermap.org/api/current?collection=current_forecast)
into OpenAPI 3.0.3 because the source page itself is HTML.

```text
spec/vendor.json                 OpenAPI transcription of the official request and response
spec/weather.smithy              reviewed Smithy trait overlay
spec/native-weather.smithy       standalone Smithy-first model used by the SDK integration tests
weather_sdk/models/_generated.py regenerated schema-derived models
weather_sdk/models/_exports.py   regenerated public schema export map
weather_sdk/_exports.py          regenerated root client/model exports
weather_sdk/__init__.py          create-once customizable facade
weather_sdk/models/current_weather_response.py create-once public model with a custom validator
weather_sdk/*/_generated/*.py    regenerated operation and aggregate-client bases
weather_sdk/_async/*.py          create-once public async extension classes
weather_sdk/_sync/*.py           create-once public sync extension classes
demo.py                          offline end-to-end usage through FakeRouter
```

From the repository root:

```bash
mise run codegen
mise run check-codegen
mise run demo-example
```

`codegen` imports the OpenAPI 3.0.3 document with pinned
`smithy-translate`, assembles `weather.smithy` with the converted model using the official Smithy
CLI, runs the direct `spitzeisen-python-client-codegen` generator in Java, generates Pydantic
models from the assembled Smithy model, and writes both operation surfaces in
one transaction. No intermediate is kept as a second source of truth. Run `mise run import-openapi`
to write the assembled Smithy JSON under `spec/generated/` when it is useful to inspect.

Files named or nested under `_generated`, plus `_exports.py` facades, are replaced on every run. The root package facade and
public model, operation, and client modules are created only when absent, so adding custom SDK
exports or behaviour there is safe. Starting with no `weather_sdk` directory recreates the entire
importable package, including `weather_sdk/__init__.py`. After that initial scaffold,
`models/current_weather_response.py` demonstrates customization with a validator requiring a
successful observation to contain at least one weather condition. The check task verifies generated
models, replaceable operation/client bases, public exports, and the presence of every public
extension module.

Generated schema classes inherit `SpitzeisenModel`: shared model policy stays centralized, aliases
remain wire-only, and unknown response members are ignored for forward compatibility.
Every type in the public response graph is re-exported through `weather_sdk.models`, so callers can
write annotations such as `from weather_sdk.models import Wind` without importing private storage.
Root response names resolve to their user-owned subclasses; nested schema names resolve to the
exact generated classes used inside those responses.

The operation requires `lat`, `lon`, and the `appid` security credential, and optionally accepts
`units`, `lang`, or a non-JSON `mode`. Standard and Spitzeisen traits in `weather.smithy` expose the
coordinates as `latitude` and `longitude`, rename `lang` to `language`, default `units`, and exclude
`mode` because Spitzeisen consumes JSON. The imported Smithy auth trait keeps `appid` out of the
method while `QueryParamAuth` supplies it at runtime. This is the same division a production SDK
would use: the assembled Smithy model describes both wire bindings and SDK presentation, while
runtime config owns credentials.
