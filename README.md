# spitzeisen

A shared Python core for handwritten REST SDKs, with async and sync clients.

An SDK owns its paths, public method signatures, Pydantic models, filters, and vendor defaults.
Spitzeisen handles HTTP client lifecycle, authentication, rate limiting, retries, pagination,
and response validation. It knows nothing about the API's domain.

```bash
pip install spitzeisen
```

Python 3.12 or newer is required. This is an alpha library; its public API can still change.

## A handwritten endpoint

```python
from spitzeisen import (
    AsyncSpitzeisenApi,
    AsyncSpitzeisenConfig,
    CursorPagination,
    QueryParamAuth,
    SpitzeisenModel,
    SpitzeisenOperationSpec,
    async_single_bucket,
    resolve_page_size,
    serialize_query_map,
)

RECORDS = SpitzeisenOperationSpec(
    path="/v1/records",
    pagination=CursorPagination(results_key="results", next_key="next_url"),
)


class Record(SpitzeisenModel):
    id: str
    name: str | None = None


class AsyncRecordsApi(AsyncSpitzeisenApi):
    async def list_records(self, *, active: bool = True, max_results: int | None = None) -> list[Record]:
        return await self.get_models(
            RECORDS,
            Record,
            params=serialize_query_map({"active": active, "limit": resolve_page_size(max_results, 1000)}),
            max_results=max_results,
        )


class AsyncExampleApi(AsyncSpitzeisenApi):
    @property
    def records_api(self) -> AsyncRecordsApi:
        return self.api(AsyncRecordsApi)


config = AsyncSpitzeisenConfig(
    base_url="https://api.example.com",
    auth=QueryParamAuth("apiKey", "YOUR_KEY"),
    limiter=async_single_bucket("example-account", 60, 60),
)

# Inside an async function:
# async with AsyncExampleApi(config) as api:
#     records = await api.records_api.list_records(max_results=100)
```

The API group is cached by class and shares the root's config, HTTP connection, and limiter.
Groups also work independently. Closing the last context closes an owned HTTP client; a supplied
HTTP client stays open unless ownership was explicitly transferred. Entering a context without
making a request does not open a connection. Use context managers to ensure cleanup.

The blocking API is `SyncSpitzeisenApi` with `SyncSpitzeisenConfig` and `sync_single_bucket`.
Its methods have the same names and arguments without `await`; `iter_records` returns an ordinary
iterator. Spitzeisen derives its sync runtime from the async implementation using `unasync` and tests
both surfaces. Handwritten SDKs can choose to ship async, sync, or both.

## Shared runtime

| Concern | Public surface |
| --- | --- |
| API groups | `api(GroupClass)` caches a group sharing the parent config |
| Raw JSON | `get_json`, `get_json_optional` (HTTP 404 becomes `None`) |
| Raw collections | `iter_records`, `get_records`, `get_records_optional` |
| Typed collections | `get_models(spec, Model, ...)`, or `validate_records(records, Model, mode)` after a raw SDK method |
| Single models | `get_model(spec, Model, result_key="results", not_found_ok=True)` |
| Validation | Ordinary Pydantic models, `raise`/`skip` list policy, optional `validate_input` |
| Pagination | `NoPagination`, `PageNumber`, `CursorPagination`, or the `PaginationStrategy` protocol |
| Auth | `BearerHeader`, `HeaderKey`, `QueryParamAuth`, `NoAuth`, or the `AuthStrategy` protocol |
| Rate limits | Scalar cost per operation; injectable async/sync limiter; local or Redis steindamm buckets |
| Serialization | Dates, timestamps, choices, path parameters, headers, repeated/comma-separated query values |
| Concurrency | `gather_bounded` for async fan-out, `map_bounded` for blocking calls |
| Tests | `FakeRouter` over httpx2's mock transport, optional pytest fixtures and operation stubs |

`max_results` caps **raw records before validation**, matching the reference SDK. The default list
validation mode is `skip`, so invalid rows can reduce the returned count. Set
`on_validation_error="raise"` on the config or per `get_models` call to receive a Pydantic error
containing all invalid row indices. HTTP failures, malformed JSON, and invalid envelopes always
raise. Raw collection methods check envelope/record shape but do not validate model fields.
`get_model` always validates; only an explicitly allowed HTTP 404 returns `None`.

Plain Pydantic `BaseModel` subclasses work. `SpitzeisenModel` is an optional base that ignores
unknown fields. `Field` constraints and custom validators always apply. For deliberately optional
constraint checking, annotate a field with `spitzeisen.models.response_constraints(...)` and set
`strict_response_validation=True`; that setting does not alter ordinary Pydantic rules.

`CursorPagination` extracts only the cursor query value from a response's `next_url`, keeps the
configured endpoint URL, and reapplies auth on every request. Subsequent requests drop initial
filters unless named in `retain_params`. For a direct response token instead of a URL, use
`CursorPagination(next_key="next_token", cursor_from_url=False)`. Missing/null continuation means
completion; malformed tokens and repeated requests raise `ResponseShapeError`. Strategy instances
are stateless and can be shared between concurrent calls. `PageNumber` stops on an empty page.

Serialize path arguments explicitly with `serialize_path_param` before passing them as keyword
arguments to JSON/collection helpers. An operation path such as `/v1/records/{record_id}` then
receives an encoded value, preventing slashes, question marks, and fragments from changing the URL.
For query mappings, `serialize_query_map` omits `None`, lowercases booleans, and repeats collection
keys. Use `serialize_query_param(values, name="filter", explode=False)` for comma-separated values.

## Other HTTP methods and response formats

`request(spec, decoder=...)` exposes the same transport for bytes, CSV, or custom decoding. The
decoder receives a buffered httpx2 response after successful HTTP status handling. For request
bodies, supply serialized `content: bytes` and a `Content-Type` header. GET, HEAD, OPTIONS, POST,
PUT, PATCH, and DELETE are supported. JSON collection helpers are intended for GET operations.

Retryable HTTP statuses are 429 and 5xx; transport retries cover timeouts and network errors.
Delays use configurable exponential backoff and a floor. POST/PATCH are not retried by default;
set `retryable=True` only when repeating the operation is safe. Every attempt pays the operation's
rate-limit cost. Typed exceptions preserve HTTP error body and headers; exhausted 429 retries
raise `MaxRetriesExceededError` with the final HTTP error as their cause. There is no automatic
`Retry-After` handling. Request timeouts apply to both owned and supplied HTTP clients.

Spitzeisen supplies no implicit vendor allowance. SDKs or applications select the bucket name,
capacity, and period; use a stable account-scoped name when sharing a Redis bucket. Logging uses
structlog without configuring the host application's logging.

## Reference SDK and development

[The handwritten example](examples/README.md) uses the local `massive-api` design as a reference:
API groups, typed and raw lists, cursor pagination, single lookups, and vendor-specific filters.
It is an executable example, not a migration or replacement of that package.
[The migration notes](docs/handwritten-sdks.md) describe the ownership boundary and remaining work
for `massive-api`.

```bash
mise run install
mise run demo-example   # offline, no credentials required
mise run build-sync
mise run check-sync
mise run lint
uv run pyright
uv run coverage run -m pytest
uv run coverage report --fail-under=80
```

An SDK can use `FakeRouter` directly or enable fixtures with
`pytest_plugins = ["spitzeisen.testing.plugin"]` in its root `conftest.py`.
The fixture-based operation stub factory also needs `pytest-mock`.

Schema-driven SDK generation is deferred. This branch has no generator CLI, Java toolchain,
Smithy build, or codegen dependency extra. The approach and recovery points are documented in
[Smithy for later](docs/smithy-future.md); the earlier branch and commit history preserve the work.

## License

MIT
