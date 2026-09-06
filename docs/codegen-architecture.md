# Code-generation architecture

Smithy is the shared semantic model. Runtime-specific Java generators consume it directly.
The `spitzeisen-python-client-codegen` plugin emits Python source; Python does not reinterpret a
serialized Smithy graph or render a second plan.

## Responsibilities

- **Ingestion:** Python launches pinned `smithy-translate` for OpenAPI input and the official
  Smithy assembler for native or imported models plus overlays.
- **Java generator:** standard Smithy model preparation, knowledge indexes, portable SDK policies,
  Python symbols/imports, supported-protocol checks, and direct async/sync client source generation.
- **Model backend:** `datamodel-code-generator` produces Pydantic classes. Both ingestion paths use the
  official `smithy-jsonschema` converter with a client-specific `JsonSchemaMapper`.
- **Python finalization:** model-name mapping, Ruff formatting, syntax checks, public model exports,
  create-once customization files, atomic per-file replacement, stale generated-file pruning,
  and reproducible drift checks.

Generation builds and validates every source before replacing destination files. Replacement is
atomic per file, not an atomic swap of the whole package.

## Smithy conventions and selective reuse

The Java plugin runs Smithy's `CodegenDirector` with a `DirectedCodegen` implementation. Smithy
prepares the service model, applies the default and dedicated input/output transforms, visits
reachable shapes in dependency order, calls the operation and service generators, and flushes
the managed writers. `SymbolProvider`, `SymbolWriter`, `WriterDelegator`, `FileManifest`,
`ServiceIndex`, `TopDownIndex`, `OperationIndex`, `HttpBindingIndex`, `NullableIndex`, and the shape
walker supply the model, binding, presence, naming, and output infrastructure.

The lifecycle follows Smithy's
[generator guidance](https://smithy.io/2.0/guides/building-codegen/implementing-the-generator.html):

1. `SpitzeisenPythonClientCodegenPlugin` validates settings and configures the director.
2. `GenerationContext` validates the supported service and resolves included `PythonOperation`
   instances. It holds the prepared model, settings, symbol provider, name scopes, and writers.
   Operations and model names are allocated in stable shape order, so dependency traversal cannot
   change collision suffixes. The context uses Smithy's cached symbol provider directly.
3. `DirectedPythonCodegen.generateOperation` invokes `PythonOperationGenerator` for each included
   operation, including operations contained in resources. `PythonParameters` owns input types and
   serialization expressions; `PythonOperationDocumentation` owns method documentation.
4. After the operation callbacks, `generateService` invokes `PythonClientGenerator` to render the
   aggregate clients. `PythonModelGenerator` then emits the response schema, public model facades,
   and build manifest in the final customization callback.
5. Smithy flushes `PythonWriterDelegator`. The delegator adds SDK ownership flags and duplicate-path
   checks to Smithy's writer management. Rendering and model-validation failures leave pending SDK
   files unwritten. Filesystem failures during flushing are not an atomic package transaction.

Structure and enum callbacks deliberately emit no Python classes: the existing Pydantic backend
consumes one response-only schema and handles nested, shared, and recursive models together.
Included unsupported unions fail capability validation; errors use the runtime's HTTP error path.
Integrations are explicitly empty. A package-private `PythonIntegration` supplies the director's
required type, without activating providers from the launcher's classpath or adding settings.

Like [smithy-python](https://github.com/smithy-lang/smithy-python), a Java target owns Python
symbols and source generation. Service-context renames use `shape.getId().getName(service)`.
A small writer adapts the upstream import/symbol approach, with collision-safe aliases and
escaped Python literals; its provenance and Apache license are recorded in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Upstream's dataclass clients and execution runtime are not drop-in replacements for Spitzeisen's
Pydantic models, HTTP runtime, and public keyword API. The generator therefore reuses Smithy's
lifecycle and core abstractions while retaining the target-specific rendering and model backend.
No upstream generator or runtime tree is vendored.

## The small build manifest

The plugin writes these temporary artifacts:

- `sdk/**/*.py`: actual source files, including create-once public subclasses.
- `manifest.json`: file ownership flags, exact response-model exports, model-name and field-alias
  mappings, compatibility diagnostics, and configured runtime dependency requirements.
- `model-schema.json`: a response-only JSON Schema sidecar for the Pydantic backend.

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

Python settings contain `external_models`, `input_adapters`, `protocol_preference`,
`require_api_required_arguments` (default false). The latter also has positive/negative CLI flags
that override the settings file for both `generate` and `check`; enabling it emits a warning in
Smithy Build and the Python CLI. `strict_response_validation` is a runtime client-config option
(default false), and is rejected in generator settings.
External models use response ShapeIds and exact module/symbol exports. Adapters map a stable ID
to a function accepting `(value, *, param_name)` and a bounded, structured public-type descriptor.
Substitution of external classes inside another generated response model is not implemented and
is rejected, rather than silently generating a different nested type.
Adapter functions may specify an import alias; collisions receive deterministic suffixes.
Dependencies are reported, never installed automatically. Unknown settings and malformed imports
or type descriptors fail generation. No plugins activate merely because they are installed.

Portable traits remain under `spitzeisen.api`: result selection, 404 behavior, rate-limit cost,
page-number pagination, sorting, client defaults, exclusions, and stable adapter
IDs. `SdkPolicy` evaluates result paths on actual Smithy members; no portable IR duplicates them.

Python presentation traits remain under `spitzeisen.python`: operation module/accessor/method,
parameter name, and Pydantic field-name overrides. Standard Smithy documentation, defaults, enum,
timestamp, and constraint traits remain authoritative.

## Supported SDK behavior

The target implements a bounded GET/HTTP/JSON subset of `alloy#simpleRestJson`, using the published
Alloy 0.3.40 model dependency. It does not claim full Alloy conformance. `AlloyProtocol` validates
included operation closures before emission. Labels (including greedy labels), repeated query
lists, query maps with explicit-binding precedence, scalar request headers, and date-time JSON
timestamps are supported. SDK policy adds result envelopes, page-number pagination, and sorting.
Vendor delimited query values use a modeled String and a Python input adapter.

Unsupported generated methods, request bodies, response-header bindings, event streams, standard
cursor pagination, nested result envelopes, unions, blobs, header collections, and additional
Alloy encoding traits fail explicitly. The old custom protocol and restJson1 handler selection
are removed. See [the capability table](alloy-client.md) for the precise boundary and migration.

Runtime configuration supplies authentication, HTTP clients, retry policy, and rate limiting.
The public `request(spec, decoder=...)` method shares that transport with handwritten operations;
it permits custom response formats and HTTP methods with replayable bytes request bodies. POST
and PATCH require an explicit retry opt-in. Generated JSON methods use the same path. Errors
preserve status, raw body, and headers, with plain-text and JSON message extraction; there is no
generated modeled exception hierarchy or X-Error-Type dispatch.

`MemberPresence` centralizes optionality using `NullableIndex.CheckMode.CLIENT`. Stronger input
signatures use server nullability while explicitly retaining `@clientOptional` precedence.
`@clientOptional` suppresses `@required` and `@default`; a contradictory portable `clientDefault`
fails generation. Modeled defaults remain available otherwise, and mutable input defaults are
copied before adapters run. None query/header values are omitted even for non-nullable annotations;
only path labels require presence at serialization. Runtime `strict_inputs` checks non-None values
when explicitly enabled. Timestamp and adapter serialization remain separate from enum validation.

`ClientSchemaMapper` rewrites response properties, required sets, and sparse collection members.
It handles properties at their containing structure because the upstream converter bypasses member
hooks for references. Upstream default emission is disabled so a referenced shape's default cannot
leak into a `@clientOptional` property. Effective defaults are then attached to member properties.
Both input signature settings use identical response presence rules.

Generated models inherit `SpitzeisenModel` and ignore unknown fields. Pydantic retains type parsing,
structure checks, and ordinary required members. The response schema always retains constraints and
enum values. After the Pydantic backend emits models, `codegen/model_validation.py` moves constraint
metadata into conditional Pydantic validators and exposes enums as open string/integer annotations.
This pass operates on emitted Python annotations; Smithy and the schema backend still own model
interpretation. Aliases, defaults, and presence rules remain on the generated fields.

Runtime clients pass `strict_response_validation` through Pydantic validation context for singular
responses, cached list adapters, and per-row retries. Nested models and collection members inherit
that context. `models.response_constraints` compiles Pydantic constraint and literal validators once,
then uses the context to decide whether to execute them after ordinary type parsing. No global
switch or class mutation is involved: the same public models serve concurrent clients with different
policies. Direct `model_validate` callers can supply the same context. Raw methods bypass Pydantic.
No synthetic error-correction values are invented for missing required response members. External
models and custom public subclasses remain responsible for their own validation policy.

OpenAPI ingestion accepts only 3.0.x and passes the document to smithy-translate without a
compatibility rewrite. All OpenAPI commands reject other versions before conversion or output
writes. OpenAPI 3.1 is disabled until a regression-tested upstream release supports it reliably;
the pinned release produces placeholder shapes even for ordinary scalar schemas.

Imported OpenAPI also uses the Smithy client schema projection, so overlays affect response models.
The pinned importer silently drops OpenAPI `nullable` and `default`; direct SDK generation rejects
these keywords in response and component schemas with a source path. Request defaults continue
to require explicit overlays (as demonstrated by the weather example). `import-openapi` remains available for inspecting the
conversion and explicitly modeling the missing semantics before native generation. This boundary
avoids silently changing the interpretation of a schema that previously went directly to Pydantic.

See the README's [required arguments and response validation](../README.md#required-arguments-and-response-validation)
for flags, the intentional typing deviation, and the None escape hatch. Both async and sync classes
are generated natively.

Implementation files under `_generated` and regenerated `_exports.py` facades are replaceable.
Public package markers and model/operation/client subclasses are created once and preserved.
Changing generated exports therefore does not require overwriting a user's root package module.

## Verification and future targets

`mise run verify-codegen` builds/checks the Java plugin and executes real native-Smithy and
OpenAPI SDK tests. Java tests cover discovery, settings/capability rejection, policy references,
safe literals/imports, service renames, resource operations, mixins, shared inputs and outputs,
recursive response models, naming stability, file ownership, and failures before writer flushing.
Python tests exercise both generated surfaces against an offline HTTP transport and cover model
validation and regeneration.

`mise run check-codegen` also checks the committed example and reproducible bundled JAR.
The JAR is thin: Coursier supplies pinned Smithy and Alloy libraries rather than embedding them.
Assembly passes Alloy's resolved model JAR explicitly to the Smithy CLI. The SDK tests consume
selected request/response cases directly from the pinned `alloy-protocol-tests` artifact; they do
not claim to run the complete upstream suite.

A future Rust generator should be another Java Smithy Build target consuming the same Model and
portable traits. Extract additional shared policy helpers when both targets need them. Do not
introduce a cross-language plan merely to prepare for that possibility. Rust generation and its
test remain intentionally deferred.
