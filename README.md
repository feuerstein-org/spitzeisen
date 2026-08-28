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
plus Coursier (`coursier`/`cs`) must be on `PATH`; this repository pins both through `mise`.

OpenAPI 3.0 and compatible OpenAPI 3.1 remain supported as an ingestion path through pinned
`smithy-translate` 0.7.8. The imported operation model is assembled with any local `.smithy`
overlays, while the original OpenAPI schema goes directly to the Pydantic backend to avoid losing
JSON Schema detail in the round trip.

A Smithy overlay supplies facts a mechanical import cannot infer reliably: rate-limit cost,
page-number pagination, public names, client defaults, and vendor-specific query serialization.
The overlay is assembled with the converted model, so stale shape references and invalid trait
applications fail before generation. Apply `@sdkOperation(generateModel: false)` and provide a
response model by hand when the source schema is incomplete or needs entirely custom behavior.

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

Use `--url` instead of `--path` to fetch an OpenAPI document. Native `--smithy` inputs and OpenAPI
`--url`/`--path` inputs are mutually exclusive. `check` is suitable for CI and compares models as
well as operations. A runnable native model is available at
[`examples/spec/native-weather.smithy`](examples/spec/native-weather.smithy). The compiler
architecture and file-by-file responsibilities are documented in
[docs/codegen-architecture.md](docs/codegen-architecture.md).

The production semantic frontend is a Smithy Build plugin written in Java; Python continues to own
the ergonomic SDK renderer and Pydantic backend. The plugin JAR is bundled with the Python package,
so generation invokes the same pinned Smithy toolchain from a source checkout or an installed
wheel. Run `mise run smithy-java-spike` for the cross-frontend regression suite, including native
Smithy, the real weather OpenAPI import, pagination, sorting, coercion, defaults, and model-property
traits. The implementation boundary is documented in
[`docs/java-smithy-frontend-spike.md`](docs/java-smithy-frontend-spike.md).

To inspect the exact intermediate model consumed by the frontend:

```bash
spitzeisen-gen import-openapi --path spec/openapi.yaml --overlay spec/sdk.smithy \
  --output-path build/vendor.smithy.json
```

Spitzeisen does not vendor or reimplement an OpenAPI parser. OpenAPI 3.1 documents that rely on
features which cannot be projected safely fail with a document path instead of generating partial
Smithy placeholder shapes.

Operation generation separates replaceable implementation from public extension points. Given an
operation named `splits`, it produces this layout for each surface:

```text
example_api/
  __init__.py       # root client/model facade; created once, safe to customize
  models/
    _generated.py    # schema-derived SpitzeisenModel classes; regenerated
    splits.py        # public Split subclass; created once, safe to customize
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
while updating the `_generated` bases. Deleting the output package and generating from scratch
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
response fields are deliberately ignored for Smithy-style forward compatibility, while all
declared fields and Pydantic constraints are validated. Aliased fields are not also populated by
field name.

Generated methods retain ergonomic keyword arguments. Input validation is permissive by default;
enable Pydantic strict validation before request serialization per client config:

```python
config = AsyncSpitzeisenConfig(
    base_url="https://api.example.test",
    strict_inputs=True,
)
```

The schema models in `models/_generated.py` are replaceable implementation. For every generated
operation model, codegen creates a public subclass such as `models/splits.py` exactly once, and the
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
select it with `spitzeisen-gen generate --base-class my_client.model_base.ClientModel`. Set
`@sdkOperation(generateModel: false)` for an entirely handwritten response model; define it under
the public `models/<operation>.py` module expected by that operation.

Generated GET operations implement a constrained generic HTTP/JSON profile. Smithy delegates
collection query serialization to a protocol, and arbitrary vendor APIs are not assumed to be AWS
`restJson1`; imported query collections therefore default to comma-separated `form` values. Set
`@pythonParameter(style: ..., explode: ...)` on the input member when the vendor uses
`spaceDelimited`, `pipeDelimited`, or repeated values. `deepObject` and `allowReserved` are not
supported. The trait's `coercion` member separately controls caller-input coercions such as `date`,
`comma_list`, or `comma_choice_list`.

### Client defaults for request params

Use Smithy's standard member `@default` when a value is part of the service model. For an SDK-only
default, `@pythonParameter(clientDefault: ...)` places a Python literal in the generated method
signature and supplies it when the caller omits the argument. A client default takes precedence and
may back a Smithy-required query or header parameter without making that parameter optional on the
wire:

```smithy
use vendor.api#ListThingsInput
use spitzeisen.api#pythonParameter

apply ListThingsInput$limit @pythonParameter(clientDefault: 100)
```

The generated method is `limit: int = 100`, while request serialization still treats `limit` as
required and rejects an explicit `None`. Client defaults should be stable, safe vendor policy;
avoid using them for identifiers, credentials, timestamps, or other context-dependent values.
Path params are keyword-only as well, so a defaulted path param can safely precede another
required path param. The generated URL still receives the default value; choose path defaults
only when silently selecting that resource is intentional.

For a generated operation that controls the number of records requested per page, name that
vendor param explicitly. `maxPageSize` may be omitted when the matching Smithy member has a
`@range` maximum:

```smithy
use vendor.api#ListThings
use spitzeisen.api#pageNumberPagination

apply ListThings @pageNumberPagination(
    page: "page"
    pageSize: "per_page"
    items: "results"
    maxPageSize: 100
)
```

This per-request setting is separate from the generated method's `max_results`, which caps the
total number of records returned across all pages.

Sorting wire names are explicit for the same reason. For separate vendor params:

```smithy
use vendor.api#ListThings
use spitzeisen.api#sorting

apply ListThings @sorting(style: "param", sort: "order_by", order: "direction")
```

For a `field.direction` value, use `style: "suffix"` and omit `order`. Generated
argument types, allowed values, and defaults come from the matching Smithy input members. When
the document does not publish them, `@sorting` can name client-owned Literal aliases and provide
defaults:

```smithy
apply ListThings @sorting(
    style: "suffix"
    sort: "order_by"
    sortLiteral: "SortField"
    orderLiteral: "SortDirection"
    sortDefault: "updated_at"
    orderDefault: "downward"
)
```

The aliases are imported from the generated client's `models` module. Spitzeisen does not
impose direction names such as `asc` and `desc`.

## License

MIT
