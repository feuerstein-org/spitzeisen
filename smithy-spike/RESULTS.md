# Smithy spike results

Tested on 2026-08-25 against the `smithy-python` develop revision from 2026-08-24.

## Decision

Smithy is a substantially better service-modeling foundation than Spitzeisen's copied OpenAPI
parser, but the current official Python generator is not a production replacement for Spitzeisen's
SDK contract. An OpenAPI-to-Smithy stage also does not produce a maintenance-free pipeline.

The practical recommendation is:

1. **Do not build another general OpenAPI-to-Smithy converter.** The existing community converter
   is the right place to contribute mappings, but its output needs validation and service-specific
   overlays. Owning a fork would replace the copied parser with a harder semantic translation layer.
2. **Do not continue Spitzeisen as a general-purpose OpenAPI compiler if minimum maintenance is the
   priority.** For production SDKs from third-party OpenAPI today, use an established OpenAPI Python
   generator directly and keep only genuinely differentiating runtime/extensions.
3. **Do not delete Spitzeisen in favor of `smithy-python` today.** Archive or pause it if desired,
   but the tested generator is explicitly unstable and is missing sync clients, paginators, runtime
   constraint validation, rate limiting, and safe handwritten extension seams.
4. If the service definition is under our control, author Smithy directly and revisit its Python
   generator when it is production-supported. OpenAPI -> Smithy -> Python is not the attractive
   route; Smithy-as-source -> multiple artifacts is.

So the answer to "can this produce something like boto3?" is **architecturally yes, currently no**.
Smithy supplies the model and code-generation framework used for AWS-style SDKs, but this repository
does not generate boto3, its Python client is async-only, and its own roadmap still lists paginators
and waiters. Boto3-quality behavior depends on a mature language generator and runtime, not merely
on expressing the service in Smithy.

## What was tested

### Handwritten Smithy

[`model/weather.smithy`](model/weather.smithy) models three representative operations:

- a GET with query parameters and query API-key authentication;
- a token-paginated GET with constraints and a custom per-operation rate-limit cost;
- a JSON POST with a timestamp, modeled errors, and a retryable throttling error.

The official generator produced 1,732 lines of Python in seven source files. The generated package
uses the released runtimes `smithy-core~=0.8.0`, `smithy-http[aiohttp]~=0.5.0`, and
`smithy-aws-core[json]~=0.11.0`.

The end-to-end test result is:

```text
5 passed, 1 xfailed
```

The passing tests cover GET and POST serialization, API-key injection, JSON deserialization,
modeled errors, and the observed absence of constraint/paginator behavior. The expected failure is
an upstream query API-key signing defect that replaces the request path with the URI password.

### OpenAPI conversion

The community `smithy-translate` 0.7.8 CLI, listed by Awesome Smithy as a **best-effort** converter,
was run on two existing repository fixtures.

| Input | Result | Can generate Python? |
| --- | --- | --- |
| `tests/fixtures/vendor.json` (OpenAPI 3.0.3) | Valid 634-line Smithy model; 48 Smithy warnings | Yes, after a protocol/auth overlay |
| `examples/spec/vendor.json` (OpenAPI 3.1.0) | Invalid model; 38 unsupported schema placeholders | No |

The OpenAPI 3.0 conversion preserved the three operations, HTTP bindings, structures, constraints,
documentation, and arbitrary vendor extensions. After the overlay in
[`converter-client/model/protocol.smithy`](converter-client/model/protocol.smithy), it generated a
4,241-line client. Its end-to-end result is:

```text
2 passed
```

The request path, dotted query names, header authentication, and JSON body all worked.

The conversion was nevertheless mechanical rather than SDK-quality:

- OpenAPI cannot choose the Smithy protocol, so `@restJson1` had to be supplied separately.
- With no auth trait, codegen 0.5.0 generated a client that reads auth fields absent from its own
  generated config. The spike adds header API-key auth as test scaffolding to bypass that defect.
- GET operations were not given `@readonly`.
- `x-polygon-paginate` remained an `@openapiExtensions` value, not `@paginated`, and no paginator
  was generated.
- Names are wire-derived (`tickerany_of`, response payload wrappers such as
  `GetStocksV1Dividends200Body`) rather than a curated public SDK vocabulary.
- The OpenAPI 3.1 fixture's normal scalar schemas became empty structures carrying
  `@errorMessage("Schema not supported ...")`. Query bindings then targeted structures and failed
  Smithy validation.

This is exactly where a custom converter would accumulate maintenance: protocol selection, auth,
pagination inference, operation semantics, naming, JSON Schema dialect differences, vendor
extensions, and lossy constructs must all become explicit policy.

## Capability comparison

| Requirement | Spitzeisen | Tested `smithy-python` 0.5.0 |
| --- | --- | --- |
| Async client | Yes | Yes |
| Sync client | Yes | No |
| GET/POST and JSON bodies | GET only currently | Yes |
| Retry pipeline | Yes | Yes |
| Modeled errors/throttling | Partial HTTP errors | Yes |
| Pagination | Runtime strategies and generated collection methods | Trait retained, no paginator emitted |
| Per-operation rate limiting | Yes | Custom trait retained, no behavior |
| Input constraints | Generated/Pydantic policy | Traits retained, not enforced at construction |
| Response validation | Pydantic, configurable raise/skip | Dataclasses; missing required response values receive defaults |
| Public method ergonomics | Named Python arguments | One generated input object per operation |
| Safe custom code | Create-once public subclasses | Generated files say `DO NOT EDIT`; generator plugin/interceptor needed |
| OpenAPI 3.0/3.1 input | Direct | Community translation; 3.1 fixture failed |
| Protocols | REST behavior implemented locally | Only `restJson1` currently supported |
| Production status | Local alpha | Upstream explicitly says unstable/not for production |

Smithy's strong points are real: its typed model, validators, traits, projections, transforms,
protocol abstraction, errors, retry metadata, and codegen integration architecture are all a better
long-term foundation than maintaining another partial OpenAPI parser. The failure is not Smithy the
modeling system; it is the readiness and fit of today's Python output for this project's promised
surface.

## Notable upstream defects observed

These were reproduced in released Python runtime dependencies and the tested upstream revision:

1. Query API-key authentication reconstructs the destination with
   `path=request.destination.password` rather than `path=request.destination.path`. The expected
   failure in [`tests/generated_client_checks.py`](tests/generated_client_checks.py) captures it.
2. For a service with no authentication traits, `ClientGenerator` always dereferences
   `config.auth_scheme_resolver` and `config.auth_schemes`, while `ConfigGenerator` only emits those
   fields when the service has an auth scheme. The converted client failed before transport until
   the overlay added an auth trait.

Both are small upstream fixes. The strategic blockers are the missing generated features and
unstable contract, not these two bugs by themselves.

## Maintenance implication

Spitzeisen currently has about 4,382 lines in `codegen/`, including about 1,560 lines of vendored
schema classes, plus roughly 1,871 lines in its runtime. Replacing its OpenAPI front end with Smithy
would delete some parsing code, but matching the existing contract would still require:

- an OpenAPI normalization/translation policy;
- a Java `PythonIntegration` or post-generator layer for naming, sync surfaces, pagination, rate
  limiting, Pydantic behavior, and extension files;
- a Python runtime or substantial Smithy runtime integration;
- ongoing compatibility work against an explicitly unstable generator.

That is likely a cleaner architecture, but not a net maintenance win today. The net win is choosing
an existing generator whose emitted SDK is acceptable, or controlling the source model and waiting
for the Smithy Python ecosystem to mature.

## Verification

- Handwritten Smithy behavioral checks: `5 passed, 1 xfailed`.
- Converted OpenAPI behavioral checks: `2 passed`.
- Checked-in generated snapshots exactly match their Smithy build output.
- `git diff --check` passes.
- The existing Spitzeisen suite reports `298 passed, 5 failed`. All five failures are in
  `tests/test_neutrality.py` and come from the already-checked-in weather example's corrupted
  signature/coercion (`unittt` and date coercion for latitude); no file outside `smithy-spike/` was
  changed by this branch.

## Artifacts

- [`generated/handwritten`](generated/handwritten): raw output from the representative Smithy model.
- [`generated/converted`](generated/converted): raw output from the valid converted OpenAPI model.
- [`converter-output-3.0/vendor.smithy`](converter-output-3.0/vendor.smithy): successful community conversion.
- [`converter-output-3.1/vendor.smithy`](converter-output-3.1/vendor.smithy): structural half of the failed conversion.
- [`converter-output-3.1/error.smithy`](converter-output-3.1/error.smithy): 38 unsupported schema placeholders.

## Upstream references

- [Smithy Python](https://github.com/smithy-lang/smithy-python): official generator and runtimes;
  its README states that the generator and generated clients are unstable and not for production.
- [Smithy Python roadmap](https://github.com/smithy-lang/smithy-python/blob/develop/ROADMAP.md): lists
  paginators and waiters as roadmap items.
- [Smithy Translate](https://github.com/disneystreaming/smithy-translate): the community converter.
- [Awesome Smithy converter listing](https://github.com/smithy-lang/awesome-smithy#model-converters):
  describes OpenAPI/JSON Schema to Smithy conversion as best-effort.
- [Smithy to OpenAPI guide](https://smithy.io/2.0/guides/model-translations/converting-to-openapi.html):
  explains the different modeling approaches and the lossy nature of conversion.
