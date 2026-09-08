# Handwritten SDK example

`handwritten_sdk` demonstrates the target shape for a thin vendor SDK. It follows a small subset
of the local `massive-api` reference and split endpoints. It is an example adapter, not a migration
or replacement of that package, and intentionally does not reproduce every field or filter.

```text
handwritten_sdk/__init__.py  vendor base URL and typed endpoint-group properties
handwritten_sdk/reference.py paths, filter defaults, raw/typed lists, optional overview
handwritten_sdk/splits.py    date bounds, comma-separated filters, compound sort
handwritten_sdk/models.py    response fields, nested aliases, custom validation
handwritten_sdk/params.py    vendor-specific Literal value sets
demo.py                     offline request and cursor walk through FakeRouter
```

From the repository root, run `mise run demo-example` or `uv run python examples/demo.py`.
The demo uses scripted HTTP responses and an example key; no account or network connection is needed.

```python
from handwritten_sdk import AsyncMarketDataApi, AsyncMarketDataConfig
from spitzeisen import BearerHeader

config = AsyncMarketDataConfig(auth=BearerHeader("your-key"))
async with AsyncMarketDataApi(config) as api:
    tickers = await api.reference_api.get_all_tickers(market="stocks", max_results=100)
    raw = await api.reference_api.get_all_tickers_raw(active=False)
    overview = await api.reference_api.get_ticker_overview("AAPL")
```

Each endpoint group subclasses `AsyncSpitzeisenApi`. The root's `api(...)` helper caches the group
and shares its configuration, so HTTP session ownership, authentication, rate limiting, retry policy,
and validation policy are configured once. Closing an owning root context closes its shared session.

The SDK chooses the URL paths, parameter names and defaults, endpoint page limits, envelope keys,
and response models. For example, the reference endpoint uses separate `sort`/`order` parameters;
the split endpoint uses `sort=execution_date.desc` and `adjustment_type.any_of=forward_split,reverse_split`.
Spitzeisen's helpers validate and serialize these choices, then walk pages and validate records.
No private runtime method, HTTP request implementation, or generated source is needed in the SDK.

The reference list shows a raw method followed by `validate_records(...)`; splits uses the combined
`get_models(...)` helper. `max_results` caps raw records inspected, so skip validation can return fewer
models. `get_ticker_overview(...)` explicitly opts into `None` for HTTP 404. Other HTTP errors, malformed
envelopes, and invalid models propagate. Unknown response fields are ignored by `SpitzeisenModel`, while
the handwritten nonblank-ticker validator and nested address alias stay with the vendor models.

`CursorPagination` extracts only the configured cursor from `next_url`, sends it to the original
operation URL, and lets the shared auth strategy add credentials again. It does not fetch arbitrary
hosts or reuse credentials from a service-provided continuation URL.

This example publishes only async operations. A handwritten blocking SDK can use `SyncSpitzeisenApi`
and `SyncSpitzeisenConfig` with the same models, descriptors, and parameter helpers; generation is not
required to use either surface. The archived Smithy direction is documented in the repository docs.
