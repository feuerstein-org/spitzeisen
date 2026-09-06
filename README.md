# spitzeisen

A neutral client framework for rate-limited REST APIs, in async **and** sync.

Every API client ends up re-implementing the same machinery: HTTP client lifecycle, retries with
backoff, rate limiting, pagination, batch validation. spitzeisen provides it once, with
pluggable rate limiting, pagination and auth — and knows nothing about the domain your API serves.

> **Alpha software.** Spitzeisen is an early project: breaking changes are expected. It currently
> supports GET operations with JSON responses only. Non-GET methods, request bodies, cookie
> params, OpenAPI Param Object `content`, and `deepObject` or `allowReserved` query
> serialization are not supported yet; they are planned for a future release.

```bash
pip install spitzeisen
```

## A complete client

```python
from pydantic import BaseModel
from spitzeisen import (
    AsyncSpitzeisenApi,
    AsyncSpitzeisenConfig,
    NoPagination,
    QueryParamAuth,
    SpitzeisenOperationSpec,
    async_single_bucket,
    serialize_query_param,
)

FORECAST = SpitzeisenOperationSpec(
    path="/data/2.5/forecast",
    pagination=NoPagination(results_key="list"),
)


class Reading(BaseModel):
    dt: int
    visibility: int | None = None


class AsyncWeatherApi(AsyncSpitzeisenApi):
    async def get_forecast(self, city: str) -> list[Reading]:
        """Five-day forecast for a city."""
        params = [
            *serialize_query_param(city, name="q"),
            *serialize_query_param("metric", name="units"),
        ]
        records = await self._get_all_pages(FORECAST, params)
        return self._validate_records(records, Reading, self.config.on_validation_error)


config = AsyncSpitzeisenConfig(
    base_url="https://api.openweathermap.org",
    auth=QueryParamAuth("appid", "YOUR_KEY"),
    # 60 calls a minute. Without a limiter the client is unlimited: spitzeisen never
    # invents an allowance for an API it knows nothing about.
    limiter=async_single_bucket("openweather", 60, 60),
)

async with AsyncWeatherApi(config) as api:
    readings = await api.get_forecast("Berlin")
```

The blocking surface is the same code with the awaits removed — it is generated from the async
one, so the two can never drift:

```python
from spitzeisen import QueryParamAuth, SyncSpitzeisenApi, SyncSpitzeisenConfig, sync_single_bucket


class SyncWeatherApi(SyncSpitzeisenApi):  # same body, without the awaits
    ...


sync_config = SyncSpitzeisenConfig(
    base_url="https://api.openweathermap.org",
    auth=QueryParamAuth("appid", "YOUR_KEY"),
    limiter=sync_single_bucket("openweather", 60, 60),
)

with SyncWeatherApi(sync_config) as api:
    readings = api.get_forecast("Berlin")
```

Anything tied to one surface carries an `Async`/`Sync` prefix, always as a prefix. An unmarked
name — `SpitzeisenOperationSpec`, every shared strategy and exception — is available to both.

## What you get

| Concern | How |
| --- | --- |
| HTTP | httpx2, confined to the request core so no httpx2 type — exception or response — reaches your operation signatures |
| Rate limiting | Any object taking a cost and acting as a context manager. Ships a smooth [steindamm](https://github.com/feuerstein-org/steindamm) bucket, local or redis-backed |
| Retries | Exponential backoff with a floor, for 429, 5xx and transport faults, raising typed errors |
| Pagination | `PageNumber`, `NoPagination`, or your own — two methods, no base class |
| Auth | `BearerHeader`, `HeaderKey`, `QueryParamAuth`, `NoAuth`, or your own |
| Validation | Batch pydantic validation with `raise`/`skip` modes |
| Logging | Structured key-value events via structlog. spitzeisen never configures logging itself |
| Testing | A pytest plugin scripting responses over `httpx2.MockTransport`, so your tests exercise the real pipeline and need no HTTP mocking library |

## Rate limiting is a protocol, not a model

spitzeisen asks only for something that can be entered as a context manager for a given cost:

```python
class AsyncLimiter(Protocol):
    def __call__(self, cost: float, /) -> AbstractAsyncContextManager[object]: ...
```

Each operation has one scalar cost. steindamm's buckets satisfy the protocol with no adapter.

## Code generation (optional)

`pip install "spitzeisen[codegen]"` adds `spitzeisen-gen`. Native Smithy is the primary input. The
official Smithy CLI assembles the model, and Smithy's official `smithy-jsonschema` library projects
the selected response closures into the existing `datamodel-code-generator` Pydantic backend. Java
25 or newer plus Coursier (`coursier`/`cs`) must be on `PATH`; this repository pins both through
`mise`. Run `spitzeisen-gen doctor` before generation to verify the installed toolchain and bundled
plugin.

OpenAPI 3.0.x is supported as an ingestion path through pinned
`smithy-translate` 0.7.8. The imported operation model is assembled with any local `.smithy`
overlays. Both source formats then use the same Smithy client schema projection for Pydantic,
so overlays and client optionality apply consistently to response models. The pinned importer
drops OpenAPI `nullable` and `default`; direct SDK generation rejects response and component
schemas containing those keywords. Request defaults need explicit Smithy overlay traits. Import
them with `import-openapi`, express the intended presence explicitly in Smithy,
and generate from that model. OpenAPI import remains a bounded, best-effort conversion.

A Smithy overlay supplies facts a mechanical import cannot infer reliably: rate-limit cost,
page-number pagination, result selection, public names, client defaults, and vendor-specific query
serialization.
The overlay is assembled with the converted model, so stale shape references and invalid trait
applications fail before generation. Portable SDK policy lives under `spitzeisen.api`; Python-only
presentation names and source layout live under `spitzeisen.python`. Existing Python models and
input conversion functions are selected in Python target configuration rather than embedded as
Smithy type or function strings. JSON services explicitly declare `alloy#simpleRestJson`.
Spitzeisen uses Alloy 0.3.40's published protocol definition and implements a bounded client subset;
unsupported features fail generation. See [Alloy support and Python extensions](docs/alloy-client.md)
for the capability table, migration steps, and custom response decoders. Alloy is a build dependency;
generated clients still run entirely in Python.

For a native Smithy project, generation is one transaction including Pydantic models and both
client surfaces. `--smithy` may be repeated when the model spans multiple files:

```bash
spitzeisen-gen generate --smithy model/service.smithy \
  --package example_api --client-name ExampleApi --output-path src/example_api
spitzeisen-gen check --smithy model/service.smithy \
  --package example_api --client-name ExampleApi --output-path src/example_api
```

For a vendor that publishes only OpenAPI, add an optional Smithy customization overlay:

```bash
spitzeisen-gen generate --path spec/openapi.yaml --overlay spec/sdk.smithy \
  --package example_api --client-name ExampleApi --output-path src/example_api
spitzeisen-gen check --path spec/openapi.yaml --overlay spec/sdk.smithy \
  --package example_api --client-name ExampleApi --output-path src/example_api
```

Target-only imports and implementations belong in a strict JSON settings file supplied to both
commands with `--python-settings`. Package, client, and vendor identity remain CLI arguments; the
settings file contains only Python target choices:

```json
{
  "external_models": {
    "example.weather#Forecast": {
      "module": "example_api.models.forecast",
      "symbol": "Forecast",
      "dependencies": ["forecast-models>=2"]
    }
  },
  "input_adapters": {
    "example.adapters#date": {
      "function": {
        "module": "example_api.params",
        "name": "coerce_date"
      },
      "public_type": {"kind": "date_input"}
    }
  },
  "protocol_preference": ["alloy#simpleRestJson"],
  "require_api_required_arguments": true
}
```

Keys in `external_models` are response ShapeIds; keys in `input_adapters` match the stable IDs used
by `spitzeisen.api#inputAdapter`. Java rejects unknown fields, malformed imports,
and invalid type descriptors before generation. Omit the file when the built-in symbol
and protocol choices are sufficient.

### Required arguments and response validation

A Smithy **trait** is an annotation that adds meaning to a model, such as `@required`,
`@default`, or `@clientOptional`. Smithy distinguishes what a server requires from what a
client can safely assume as the API evolves. The two settings below are independent:

| Setting | Configured at | Default | When enabled |
| --- | --- | --- | --- |
| `require_api_required_arguments` | Generation | `false` | API-required input keywords have non-nullable types and no Python default unless a modeled or configured default exists. |
| `strict_response_validation` | Runtime, per client config | `False` | Pydantic also enforces modeled ranges, lengths, patterns, and known enum values. |

With the first setting disabled, top-level Smithy inputs use optional types, for example
`query: str | None = None`. With it enabled, an API-required query becomes `query: str`.
An explicitly `@clientOptional` member stays optional in **both** modes, and its Smithy default
is ignored. Other defaults are retained: `limit: int | None = 10` becomes `limit: int = 10`.
A custom `clientDefault` conflicting with `@clientOptional` is a generation error.

The stronger input typing is an intentional opt-in departure from Smithy's usual client
optionality. It describes the API contract at generation time. If the API later stops requiring
an argument, callers can explicitly omit its wire value using a local type-checker suppression:

```python
await api.search_api.search(query=None)  # type: ignore[arg-type]
```

The same applies to headers. Calling `search()` still raises Python's missing-argument error
when `query` has no default. Path labels require a non-empty value before any network request.
Timestamp formatting, configured input adapters, URL encoding, and sort-pair serialization still
run. Generated code does not automatically reject unknown input enum values. `strict_inputs=True`
remains a separate runtime option for checking non-None inputs; it preserves the None escape hatch.

Response models always parse field types and nested structures, reject missing ordinary required
members, and ignore unknown fields. Missing optional members become `None`; only declared defaults
are filled in. Sparse lists/maps allow `None` elements/values; dense collections do not. By default,
response enums are open `str`/`int` values and range/length/pattern checks are skipped so compatible
API changes remain usable. Enable those checks on the same generated package at runtime:

```python
config = AsyncSpitzeisenConfig(
    base_url="https://api.example.test",
    strict_response_validation=True,
)
```

`SyncSpitzeisenConfig` accepts the same option. Changing it affects subsequent validation calls;
no package regeneration is needed. Generated models retain all supported constraints and known
enum values, while their public types remain the same in both modes. The client passes this
policy through Pydantic validation context, including to nested models and collection members.
Concurrent clients can choose different policies without changing shared model classes.
Optionality, unknown-field handling, and Pydantic's normal type parsing stay the same.
This setting is separate from Pydantic's `strict=True`, which controls type coercion.

Direct model validation defaults to the same compatible policy. To opt in explicitly:

```python
result = ResponseModel.model_validate(payload, context={"strict_response_validation": True})
```

For collection results, `on_validation_error="skip"` or `"raise"` controls handling of invalid rows;
single-result methods raise validation errors. `*_raw` methods bypass Pydantic validation while
retaining HTTP response-shape checks. External and handwritten models retain their own validators.

The input-signature setting is accepted by `generate` and `check` as paired flags:

```bash
--require-api-required-arguments / --no-require-api-required-arguments
```

Explicit flags override the JSON file; omitted flags preserve its settings. Use the same settings
for generation and drift checks. Enabling stronger input typing emits a compatibility warning;
`--fail-on-warning` also treats this warning as a failure. The checked-in weather example opts
into API-required typing in `examples/spec/python.json`. Response validation belongs in runtime
config; the former generator setting and CLI flags are no longer accepted. Packages generated
before this runtime option was introduced need one regeneration to include the optional checks.

These choices follow Smithy's [member optionality rules](https://smithy.io/2.0/spec/aggregate-types.html#structure-member-optionality)
and [clientOptional semantics](https://smithy.io/2.0/spec/type-refinement-traits.html#clientoptional-trait).
The opt-ins and their diagnostics follow the [generator interoperability guidance](https://smithy.io/2.0/guides/building-codegen/mapping-shapes-to-languages.html#interoperability).

Use `--url` instead of `--path` to fetch an OpenAPI document. Native `--smithy` inputs and OpenAPI
`--url`/`--path` inputs are mutually exclusive. `check` is suitable for CI and compares models as
well as operations. A runnable native model is available at
[`examples/spec/native-weather.smithy`](examples/spec/native-weather.smithy). The compiler
architecture and file-by-file responsibilities are documented in
[docs/codegen-architecture.md](docs/codegen-architecture.md).

The Java Smithy Build plugin `spitzeisen-python-client-codegen` directly emits Python operations,
aggregate clients, and public extension scaffolds using Smithy's symbol and writer infrastructure.
It passes Python generated files plus a small model-build manifest, not a runtime-neutral plan or
a Python rendering plan. Python retains the Pydantic backend, source formatting, and file management.
One explicit model-name mapping keeps service renames and generated imports consistent.

The reproducible `spitzeisen-python-codegen.jar` is bundled with the Python package. Run
`mise run verify-codegen` to build it and execute native Smithy and imported OpenAPI SDK tests.
Future runtime targets can be separate Java Smithy Build plugins sharing the semantic model and
portable policies. No Rust implementation is included yet.

To inspect the exact intermediate model consumed by the frontend:

```bash
spitzeisen-gen import-openapi --path spec/openapi.yaml --overlay spec/sdk.smithy \
  --output-path build/vendor.smithy.json
```

Spitzeisen does not vendor or reimplement an OpenAPI parser. `generate`, `check`, and
`import-openapi` accept only OpenAPI 3.0.x. OpenAPI 3.1 is disabled because the pinned
smithy-translate release produces placeholder shapes even for ordinary scalar schemas.
Unsupported versions fail before conversion or output writes; no automatic downgrade is performed.
Export a valid OpenAPI 3.0.x document or use native Smithy. Changing a 3.1 document's version field
alone is not a general conversion strategy. Support can be reconsidered once an upstream release
passes 3.1 regression tests.

Operation generation separates replaceable implementation from public extension points. Given an
operation named `splits`, it produces this layout for each surface:

```text
example_api/
  __init__.py       # root client/model facade; created once, safe to customize
  models/
    _generated.py    # schema-derived SpitzeisenModel classes; regenerated
    split.py         # public Split subclass; created once, safe to customize
  _async/
    _generated/
      splits.py       # AsyncSplitsApiBase; regenerated
      client.py       # AsyncExampleApiBase and operation wiring; regenerated
    splits.py         # AsyncSplitsApi; created once, safe to customize
    client.py         # AsyncExampleApi; created once, safe to customize
  _sync/
    _generated/
      splits.py       # SyncSplitsApiBase; regenerated
      client.py       # SyncExampleApiBase and operation wiring; regenerated
    splits.py         # SyncSplitsApi; created once, safe to customize
    client.py         # SyncExampleApi; created once, safe to customize
```

The root package facade and public model, operation, and client files can remain as scaffolded or
carry SDK-specific exports, validators, and behaviour. Running generation again preserves them
while updating the `_generated` bases and `_exports.py` facades. Deleting the output package and
generating from scratch
recreates every required `__init__.py`, including the root facade that exports both clients and
generated response models. The aggregate client shares one config across its operation properties
and owns their context-manager lifecycle:

```python
from example_api import AsyncExampleApi
from spitzeisen import AsyncSpitzeisenConfig

config = AsyncSpitzeisenConfig(base_url="https://api.example.test")
async with AsyncExampleApi(config) as api:
    splits = await api.splits_api.get_splits()
```

Schema-derived models inherit `SpitzeisenModel`, which centralizes shared model policy. Unknown
response fields are deliberately ignored for Smithy-style forward compatibility. Declared field
types are parsed and validated; modeled constraints and known enum checks require the runtime
config option `strict_response_validation=True`. Aliased fields are not also populated by
field name.

Generated methods retain ergonomic keyword arguments. Input validation is permissive by default;
enable Pydantic strict validation of non-None values before request serialization per client config:

```python
config = AsyncSpitzeisenConfig(
    base_url="https://api.example.test",
    strict_inputs=True,
)
```

The schema models in `models/_generated.py` are replaceable implementation. For every generated
response shape, codegen creates a public subclass such as `models/split.py` exactly once, and the
operation imports that public class. Model-specific validators therefore live safely outside
generated code:

```python
from pydantic import model_validator
from example_api.models._generated import Split as GeneratedSplit


class Split(GeneratedSplit):
    @model_validator(mode="after")
    def check_vendor_invariant(self) -> "Split":
        ...
        return self
```

Codegen also maintains a public facade for every schema type reachable from those responses.
Callers can therefore annotate nested values without importing private storage:

```python
from weather_sdk.models import CurrentWeatherResponse, Wind
```

Root response names such as `Split` resolve to their client-owned public subclasses. Nested schema
names resolve to the exact generated classes used by those responses.

A client needing shared behaviour can still provide a base derived from `SpitzeisenModel` and
select it with `spitzeisen-gen generate --base-class my_client.model_base.ClientModel`. An entirely
handwritten response type is registered by response ShapeId under `external_models` in the
`--python-settings` file; its module, symbol, and dependencies remain Python target configuration
and are validated by the Java target.

Generated GET operations implement the supported `alloy#simpleRestJson` subset. Query lists use
repeated keys, as specified by Smithy HTTP bindings. A vendor's comma-, pipe-, or space-separated
parameter is modeled as a `String`; a Python adapter can accept a list and serialize that string.
The legacy `spitzeisen.api#queryEncoding` trait is rejected by this target.
Nonstandard caller-input conversions use portable `spitzeisen.api#inputAdapter(id: ...)`; the
Python target resolves that stable ID through `input_adapters` in the `--python-settings` file to a
public type and an exact function import.

### Client defaults for request params

Use Smithy's standard member `@default` when a value is part of the service model. For an SDK-only
default, `spitzeisen.api#clientDefault(value: ...)` places a value in the generated
method signature and supplies it when the caller omits the argument. A client default takes
precedence and may back a Smithy-required query or header parameter without making that parameter
optional on the wire:

```smithy
use vendor.api#ListThingsInput
use spitzeisen.api#clientDefault

apply ListThingsInput$limit @clientDefault(value: 100)
```

The generated method is `limit: int = 100`, while request serialization still treats `limit` as
required and rejects an explicit `None`. Client defaults should be stable, safe vendor policy;
avoid using them for identifiers, credentials, timestamps, or other context-dependent values.
Path params are keyword-only as well, so a defaulted path param can safely precede another
required path param. The generated URL still receives the default value; choose path defaults
only when silently selecting that resource is intentional.

For a generated operation that controls the number of records requested per page, name that vendor
member explicitly. The optional `pageSizeMember` gets its ceiling from the matching member's
standard `@range(max: ...)`; the logical records path is the independent `result` policy:

```smithy
use vendor.api#ListThings
use vendor.api#ListThingsInput
use smithy.api#range
use spitzeisen.api#pageNumberPagination
use spitzeisen.api#result

apply ListThingsInput$per_page @range(max: 100)
apply ListThings @pageNumberPagination(
    pageMember: "page"
    pageSizeMember: "per_page"
)
apply ListThings @result(path: ["results"])
```

This per-request setting is separate from the generated method's `max_results`, which caps the
total number of records returned across all pages.

Sorting wire names are explicit for the same reason. For separate vendor params:

```smithy
use vendor.api#ListThings
use spitzeisen.api#sorting

apply ListThings @sorting(
    encoding: "separate"
    sortMember: "order_by"
    orderMember: "direction"
)
```

For a `field.direction` value, use `encoding: "suffix"` and omit `orderMember`. Generated
argument types and allowed values come from the matching Smithy enum input member. Each enum value
must contain the field and direction separated by the configured separator. Defaults come from the
standard Smithy member `@default` trait:

```smithy
use vendor.api#ListThings
use vendor.api#ListThingsInput
use smithy.api#default
use spitzeisen.api#sorting

apply ListThingsInput$order_by @default("updated_at.downward")
apply ListThings @sorting(
    encoding: "suffix"
    sortMember: "order_by"
    separator: "."
)
```

The Java Python generator splits that closed wire enum into `sort` and `order` Literal arguments.
Spitzeisen does not impose direction names such as `asc` and `desc`.

## License

MIT
