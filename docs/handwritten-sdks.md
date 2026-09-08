# Handwritten SDK boundary

The immediate goal is a shared runtime that removes duplicated machinery from SDKs shaped like
`massive-api`. It does not require a service specification or introduce a new endpoint-definition
language. Handwritten methods remain the place to see the complete request and its return type.

This is a good fit for the current clients: their endpoint code is mostly stable vendor policy,
while their base/config/helpers implement the same transport and validation behavior. Extracting
that machinery delivers reuse without committing to supporting every OpenAPI or Smithy feature.

## Ownership

| Spitzeisen | SDK |
| --- | --- |
| HTTP connection lifecycle and shared API groups | Vendor base URL, convenient config constructor and public group properties |
| Retry/error handling and per-attempt limiter use | Account tier defaults, bucket identity, operation costs, retry overrides |
| Pagination loop and strategies | Results envelope, cursor/page parameter names and vendor page-size limits |
| Raw/model collection and single-object helpers | Pydantic types, aliases, domain validators and absence policy |
| Reusable parameter serialization/coercion | Public Literal choices, filter names, sort format and request defaults |
| Test transport and generic runtime regressions | Endpoint wire-contract and model tests |

No vendor name or financial-data model belongs in the installed core. In particular, an API's
subscription allowance and authentication scheme are policy supplied to the runtime, not inferred.
A handwritten SDK may keep substantial models and docstrings; “thin” means transport behavior is
not copied into every SDK, not that domain code must fit in a few lines.

## What was reused

The initial implementation at `a2f2674` already extracted much of the reference SDK into neutral
auth, rate-limit and pagination strategies, a shared request path, list validation and sync
conversion. `add3716` demonstrates this with a handwritten weather client. The current base
`0f2d1a6` also supports custom response decoders, replayable request bodies, explicit retry safety,
and HTTP error metadata. Those improvements are retained.

This branch adds the missing cursor strategy, public raw/typed helper methods, reusable page-size
selection, and typed cached API groups. It also guards each pagination walk against cycles and
allows a root client to reopen its owned HTTP connection after its last context has exited.

## Applying it to massive-api

The executable `examples/handwritten_sdk` package uses the existing local SDK's paths and patterns.
It exercises reference listings, optional overview responses, and split filters over the real
Spitzeisen transport with scripted responses. It deliberately covers a subset; the actual
`~/dev/massive-api` checkout is unchanged.

A follow-up migration can retain the vendor package's public names while:

1. Replacing `BaseMassiveApi` with a small subclass of `AsyncSpitzeisenApi` and constructing an
   `AsyncSpitzeisenConfig` with the vendor URL, auth, and limiter policy.
2. Keeping `reference_api`, `splits_api`, and `dividends_api` properties, implemented using
   `self.api(GroupClass)`; the core now owns caching and the context lifecycle.
3. Declaring operation specs using `CursorPagination(results_key="results", next_key="next_url")`.
   Cursor follow-ups send only the opaque cursor and freshly applied auth. The response's host
   and path are never followed.
4. Implementing raw lists through `get_records`, and typed methods through `get_models` or
   `validate_records` after their existing raw method. `max_results` still counts raw rows before
   skip validation; page size is `resolve_page_size(max_results, vendor_maximum)`.
5. Calling `get_model(..., result_key="results", not_found_ok=True)` for optional lookups, or
   retaining an SDK-owned envelope transformation for more unusual responses. Existing Pydantic
   `AliasPath` and custom validators remain SDK code.
6. Moving request/retry/limiter tests to the core contract and retaining all endpoint filters,
   defaults, response types and user-facing compatibility tests in the vendor package.

## Compatibility decisions for that migration

- Spitzeisen uses httpx2; the current SDK uses aiohttp. A supplied `session` cannot transparently
  become an httpx2 client. Preserve or explicitly migrate that public customization point.
- Spitzeisen applies `request_timeout` even to a supplied HTTP client. The current SDK documents
  that custom sessions keep their own timeout. Account for this before claiming drop-in behavior.
- Keep the SDK's convenience API-key constructor and subscription defaults in the SDK. Use a
  deterministic digest or explicit account key for limiter sharing; Python's process-randomized
  `hash(api_key)` is unsuitable for naming a distributed bucket.
- `coerce_choices` returns a collection, not the reference SDK's prejoined string. Serialize
  `*.any_of` filters with `explode=False`. Validate vendor-owned sort/order Literals using
  `coerce_choice` before combining them with `coerce_sort`.
- Use `serialize_path_param` for identifiers. It encodes path separators and reserved characters;
  operation specs expect already serialized values.
- The core rejects missing collection keys and non-object rows instead of interpreting every
  unexpected envelope as an empty result. Explicit null collection values remain empty lists.
  A vendor that intentionally omits an empty collection can supply its own pagination strategy.
- Single-model helpers treat null/missing success envelopes as malformed, independently of their
  optional HTTP 404 policy. Keep a vendor-specific mapping if its contract says otherwise.
- Existing plain `BaseModel` classes work. Shared model inheritance is optional; keep domain
  constraints and defaults intact rather than adopting schema-generation validation policies.

No new cross-repository dependency is established by this example. Update the organization's
canonical repository map when the vendor package actually adopts Spitzeisen.
