# spitzeisen

A neutral client framework for rate-limited REST APIs, in async **and** sync.

Every API client ends up re-implementing the same machinery: HTTP client lifecycle, retries with
backoff, rate limiting, pagination, batch validation. spitzeisen provides it once, with
pluggable rate limiting, pagination and auth — and knows nothing about the domain your API serves.

> **Alpha software.** Spitzeisen is an early project: breaking changes are expected. It currently
> supports GET endpoints with JSON responses only. Non-GET methods, request bodies, cookie
> parameters, OpenAPI Parameter Object `content`, and `deepObject` or `allowReserved` query
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
    SpitzeisenEndpointSpec,
    async_single_bucket,
    serialize_query_param,
)

FORECAST = SpitzeisenEndpointSpec(
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
name — `SpitzeisenEndpointSpec`, every shared strategy and exception — is available to both.

## What you get

| Concern | How |
| --- | --- |
| HTTP | httpx2, confined to the request core so no httpx2 type — exception or response — reaches your endpoint signatures |
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

Each endpoint has one scalar cost. steindamm's buckets satisfy the protocol with no adapter.

## Code generation (optional)

`pip install "spitzeisen[codegen]"` adds `spitzeisen-gen`, which builds models and endpoint
modules from an OpenAPI document plus a small manifest describing what OpenAPI cannot: what each
call costs, how it paginates, the largest page it will serve. The OpenAPI document is optional —
the manifest alone is enough, which matters for the many APIs that publish no spec.

Endpoint generation separates replaceable implementation from public extension points. Given an
endpoint named `splits`, it produces this layout for each surface:

```text
example_api/
  models/
    _generated.py    # schema-derived SpitzeisenModel classes; regenerated
    splits.py        # public Split subclass; created once, safe to customize
  _async/
    _generated/
      splits.py       # AsyncSplitsApiBase; regenerated
      client.py       # AsyncExampleApiBase and endpoint wiring; regenerated
    splits.py         # AsyncSplitsApi; created once, safe to customize
    client.py         # AsyncExampleApi; created once, safe to customize
  _sync/
    _generated/
      splits.py       # SyncSplitsApiBase; regenerated
      client.py       # SyncExampleApiBase and endpoint wiring; regenerated
    splits.py         # SyncSplitsApi; created once, safe to customize
    client.py         # SyncExampleApi; created once, safe to customize
```

The public model, endpoint, and client files can remain empty subclasses or carry SDK-specific
validators and behaviour. Running generation again preserves them while updating the `_generated`
bases. The aggregate client shares one config across its endpoint properties and owns their
context-manager lifecycle:

```python
from example_api._async.client import AsyncExampleApi
from spitzeisen import AsyncSpitzeisenConfig

config = AsyncSpitzeisenConfig(base_url="https://api.example.test")
async with AsyncExampleApi(config) as api:
    splits = await api.splits_api.get_splits()
```

Schema-derived models inherit `SpitzeisenModel`, which centralizes shared model policy while
currently retaining Pydantic's defaults: unknown fields are ignored, and aliased fields are not
also populated by field name. Keeping that policy in one shared base avoids repeating
`model_config` in every generated class.

The schema models in `models/_generated.py` are replaceable implementation. For every generated
endpoint model, codegen creates a public subclass such as `models/splits.py` exactly once, and the
endpoint imports that public class. Model-specific validators therefore live safely outside
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
select it with `spitzeisen-gen models --base-class my_client.model_base.ClientModel`. Set
`generate_model: false` for an entirely handwritten response model; define it under the public
`models/<endpoint>.py` module expected by that endpoint.

Generated GET endpoints honor OpenAPI query serialization for arrays and objects: `form`,
`spaceDelimited`, and `pipeDelimited`, including `explode`. `deepObject` and `allowReserved`
query serialization are not supported yet. Query values are always percent-encoded by httpx2.
For a manifest-only API, set OpenAPI's `style` and `explode` on a declared query parameter when
its wire representation differs from the defaults. Use `coercion_style` for Spitzeisen's
caller-input coercions such as `date`, `comma_list`, or `comma_choice_list`.

For a generated endpoint that controls the number of records requested per page, name that
vendor parameter explicitly. `max_page_size` may be omitted when the matching OpenAPI schema
declares its maximum:

```yaml
pagination: page_number
page_size_param: per_page
max_page_size: 100
```

This per-request setting is separate from the generated method's `max_results`, which caps the
total number of records returned across all pages.

Sorting wire names are explicit for the same reason. For separate vendor parameters:

```yaml
sort_style: param
sort_param: order_by
order_param: direction
```

For a `field.direction` value, use `sort_style: suffix` and omit `order_param`. Generated
argument types, allowed values, and defaults come from the matching OpenAPI parameters. When
the document does not publish them, the manifest can name client-owned Literal aliases and
provide defaults:

```yaml
sort_style: suffix
sort_param: order_by
sort_literal: SortField
order_literal: SortDirection
sort_default: updated_at
order_default: downward
```

The aliases are imported from the generated client's `models` module. Spitzeisen does not
impose direction names such as `asc` and `desc`.

## License

MIT
