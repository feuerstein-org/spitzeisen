# Code-generation architecture

Smithy is the shared semantic model. Runtime-specific Java generators consume it directly.
The `spitzeisen-python-client-codegen` plugin emits Python source; Python does not reinterpret a
serialized Smithy graph or render a second plan.

## Responsibilities

- **Ingestion:** Python launches pinned `smithy-translate` for OpenAPI input and the official
  Smithy assembler for native or imported models plus overlays.
- **Java generator:** standard Smithy model preparation, knowledge indexes, portable SDK policies,
  Python symbols/imports, supported-protocol checks, and direct async/sync client source generation.
- **Model backend:** `datamodel-code-generator` produces Pydantic classes. Native Smithy uses the
  official `smithy-jsonschema` converter; OpenAPI uses the original schema to retain schema detail.
- **Python finalization:** model-name mapping, Ruff formatting, syntax checks, public model exports,
  create-once customization files, atomic per-file replacement, stale generated-file pruning,
  and reproducible drift checks.

Generation builds and validates every source before replacing destination files. Replacement is
atomic per file, not an atomic swap of the whole package.

## Smithy conventions and selective reuse

The Java plugin uses Smithy's `CodegenDirector.simplifyModelForServiceCodegen` and dedicated
input/output transforms, `SymbolProvider`, `SymbolWriter`, `FileManifest`, `ServiceIndex`,
`TopDownIndex`, `OperationIndex`, `HttpBindingIndex`, `NullableIndex`, and the shape walker.
This reuses maintained model semantics, binding resolution, timestamp precedence, and output
infrastructure rather than copying them.

Like [smithy-python](https://github.com/smithy-lang/smithy-python), a Java target owns Python
symbols and source generation. Service-context renames use `shape.getId().getName(service)`.
A small writer adapts the upstream import/symbol approach, with collision-safe aliases and
escaped Python literals; its provenance and Apache license are recorded in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

We do **not** vendor the full upstream generator or runtime, implement a speculative integration
framework, or generate a second object-model backend. Upstream's dataclass clients and execution
runtime are not drop-in replacements for Spitzeisen's Pydantic models, HTTP runtime, and public
keyword API. The full DirectedCodegen callback framework is unnecessary for this bounded target;
its standard model transforms and core writer/symbol infrastructure are used directly.

## The small build manifest

The plugin writes these temporary artifacts:

- `sdk/**/*.py`: actual source files, including create-once public subclasses.
- `manifest.json`: file ownership flags, exact response-model exports, model-name and field-alias
  mappings, selected OpenAPI model paths, and configured runtime dependency requirements.
- `model-schema.json`: a response-only JSON Schema sidecar for the native Pydantic backend.

There are no operation bindings, shape graphs, type descriptors, or policy nodes in the manifest.
It is an unversioned build interface between tools shipped together, not a user-authored
specification. Python validates it using Pydantic and rejects invalid source paths.

Java's symbol provider supplies a single name mapping to the model backend through
`--model-name-map`. This keeps renamed/collision-resolved response classes consistent with imports
and public model subclasses. Pydantic remains responsible for recursive/nested model definitions,
constraints, enums, and response validation. Generated model exports are based on classes the
backend actually emitted; missing required response classes fail the build.

## Settings and policies

Plugin settings are `service` (optional when unambiguous), `package`, `client_name`, optional
`vendor`, and optional `python`. The CLI supplies the first four; `--python-settings` loads the
nested Python object. Java validates it once.

Python settings contain `external_models`, `input_adapters`, and `protocol_preference`.
External models use response ShapeIds and exact module/symbol exports. Adapters map a stable ID
to a function accepting `(value, *, param_name)` and a bounded, structured public-type descriptor.
Substitution of external classes inside another generated response model is not implemented and
is rejected, rather than silently generating a different nested type.
Adapter functions may specify an import alias; collisions receive deterministic suffixes.
Dependencies are reported, never installed automatically. Unknown settings and malformed imports
or type descriptors fail generation. No plugins activate merely because they are installed.

Portable traits remain under `spitzeisen.api`: result selection, 404 behavior, rate-limit cost,
page-number pagination, sorting, query encoding, client defaults, exclusions, and stable adapter
IDs. `SdkPolicy` evaluates result paths on actual Smithy members; no portable IR duplicates them.

Python presentation traits remain under `spitzeisen.python`: operation module/accessor/method,
parameter name, and Pydantic field-name overrides. Standard Smithy documentation, defaults, enum,
timestamp, and constraint traits remain authoritative.

## Supported SDK behavior

The current target implements a constrained GET/HTTP/JSON profile for
`spitzeisen.protocols#genericRestJson` and `aws.protocols#restJson1`. It does not claim full
restJson1 protocol conformance. Labels (including greedy labels), query parameters, query maps,
headers, supported timestamp formats, explicit collection query styles, result envelopes,
page-number pagination, and both sorting encodings are supported.

Unsupported methods, request bodies, response-header bindings, event streams, standard cursor
pagination, nested result envelopes, `deepObject`, and `allowReserved` fail explicitly in the
Python Java target. Runtime configuration supplies authentication, HTTP clients, retry policy, and
rate limiting; generated code does not synthesize an auth runtime or modeled exception hierarchy.

Generated models inherit `SpitzeisenModel`; unknown response fields are ignored and declared
constraints are validated. Required inputs stay required even when a default is provided, and
optional strict input validation remains configurable. Mutable input defaults are copied before
adapter calls. Both async and sync classes are generated natively.

Implementation files under `_generated` and regenerated `_exports.py` facades are replaceable.
Public package markers and model/operation/client subclasses are created once and preserved.
Changing generated exports therefore does not require overwriting a user's root package module.

## Verification and future targets

`mise run verify-codegen` builds/checks the Java plugin and executes real native-Smithy and
OpenAPI SDK tests. Java tests cover discovery, settings/capability rejection, policy references,
safe literals/imports, service renames, and shared response models. Python tests exercise both
generated surfaces against an offline HTTP transport and cover model validation and regeneration.

`mise run check-codegen` also checks the committed example and reproducible bundled JAR.
The JAR is thin: Coursier supplies pinned Smithy libraries rather than embedding them.

A future Rust generator should be another Java Smithy Build target consuming the same Model and
portable traits. Extract additional shared policy helpers when both targets need them. Do not
introduce a cross-language plan merely to prepare for that possibility. Rust generation and its
test remain intentionally deferred.
