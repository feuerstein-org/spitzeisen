# Code-generation architecture

Spitzeisen uses a two-stage compiler boundary. Java owns Smithy semantics and emits a
target-neutral `ServicePlan`; Python owns target naming, protocol behavior, capability checks, and
the renderer-ready `PythonPlan`.

```text
native .smithy sources ──────────────────────────────┐
                                                    │
vendor OpenAPI ─ compatibility projection ─ smithy-translate 0.7.8
      │                                             │
      │                                             ▼
      │                                      converted Smithy JSON
      │                                             │
      │                 local .smithy overlays ─────┤
      │                                             ▼
      │                                  official Smithy assembler
      │                                             │
      │                                      Smithy 2.0 model
      │                                             ▼
      │                              spitzeisen-service-plan
      │                          prepared Model + knowledge indexes
      │                                  │                 │
      │                                  ▼                 ▼
      │                          service-plan.json   model-schema.json
      │                                  │          temporary sidecar
      │                                  ▼                 │
      │                       strict Python ServicePlan     │
      │                                  ▼                 │
      │                   integrations → generation context│
      │                                  ▼                 │
      │                    symbol provider + protocol      │
      │                                  ▼                 │
      │                      Python target lowering        │
      │                                  ▼                 │
      │                           PythonPlan                │
      │                           │             │           │
      │                           ▼             └─────┬─────┘
      │                     Jinja clients             ▼
      │                                      Pydantic model backend
      │
      └─────────────────────────────────── original OpenAPI schema
```

The internal ServicePlan is a flat, unversioned document with `service`, `operations`, `shapes`,
and `extensions` members. It is generated and consumed by components shipped together; it is not a
user-maintained manifest or a compatibility promise. Smithy sources and traits remain the source of
truth.

The plan keeps exact Smithy kinds and ShapeId edges instead of collapsing them into Python-like
types. It carries dedicated inputs and outputs, requiredness, client nullability, defaults,
constraints, recursive-shape information, enum names and values, HTTP request/response/error
bindings, protocols, auth schemes, modeled errors, event streams, and normalized portable policy.
Python identifiers, annotations, modules, imports, coercion calls, and Pydantic decisions are absent.
Namespaced target extensions are preserved as raw nodes for the owning target to interpret.

## Model inputs and Smithy assembly

`spitzeisen-gen generate --smithy model/service.smithy` is the direct path. `--smithy` may be
repeated and combined with repeatable `--overlay` sources. Native input never invokes an OpenAPI
converter.

`src/spitzeisen/codegen/openapi.py` launches the pinned Maven artifact
`com.disneystreaming.smithy:smithytranslate-cli_2.13:0.7.8`. Its OpenAPI 3.1 path cannot safely
represent every JSON Schema feature, so Spitzeisen first projects the explicitly supported subset
to OpenAPI 3.0.3. Nullable scalar unions, `const`, and numeric exclusive bounds have explicit
mappings. Genuinely unsupported 3.1 features fail with their document path.

`src/spitzeisen/codegen/assembly.py` launches the official Smithy CLI pinned in
`src/spitzeisen/codegen/smithy/toolchain.properties`. It assembles:

- native sources or the converted Smithy JSON;
- the bundled `spitzeisen.api` and `spitzeisen.python` trait definitions plus the
  `spitzeisen.protocols#genericRestJson` protocol definition; and
- every repeatable overlay.

Smithy's assembler therefore validates target trait selectors and referenced shapes before either
compiler stage runs. A vendor rename fails assembly instead of silently dropping a customization.

`spitzeisen-gen import-openapi` writes the fully assembled Smithy JSON consumed by the frontend.
Normal `generate` and `check` commands perform the same import and assembly in temporary
directories.

## Java ServicePlan frontend

The `spitzeisen-service-plan` Smithy Build plugin accepts only an optional `service` setting. Package
name, client name, vendor label, output location, Python integrations, and protocol preferences never
enter Java settings.

Before compilation, Java applies the standard service-codegen preparation sequence:

- copy service errors to operations;
- flatten and remove mixins; and
- create dedicated input and output structures.

`ServicePlanCompiler` then uses Smithy's `TopDownIndex`, `OperationIndex`, `HttpBindingIndex`,
`ServiceIndex`, `NullableIndex`, `PaginatedIndex`, `EventStreamIndex`, `TopologicalIndex`, and shape
walker. The resulting `service-plan.json` uses ShapeId-keyed operation and shape maps. Standard
Smithy traits remain preserved, common constraints and portable Spitzeisen policies are also
normalized for consumers, and `spitzeisen.python` traits are copied only into extension maps.

Java deliberately does not reject a valid model because the current Python runtime lacks a
feature. POST operations, request bodies, standard Smithy pagination, event streams, and other
semantics remain representable in ServicePlan. Java errors are reserved for invalid Smithy,
unresolvable references, or contradictions in portable policy.

The plugin also emits `model-schema.json` using official `smithy-jsonschema`. This is a temporary
native-Pydantic sidecar scoped to neutral response result roots, not part of the ServicePlan type
system. The exact shape graph in ServicePlan is the durable runtime-neutral boundary. OpenAPI input
continues to send the original vendor document to the Pydantic backend because translation can lose
JSON Schema detail.

## Python target pipeline

`service_plan_io.py` validates the Java document into immutable structures from `service_plan.py`.
`lower_service_plan` then creates a `PythonGenerationContext` in a deterministic sequence:

1. select only integrations named by `PythonSettings.enabled_integrations`;
2. allow those integrations to preprocess the target view of ServicePlan;
3. create the `PythonSymbolProvider` and apply integration decorators;
4. register integration-provided protocols;
5. resolve one handler for a protocol actually declared by the service; and
6. lower and validate the Python surface into `PythonPlan`.

The CLI decodes the optional `--python-settings` JSON document into `PythonSettings` before this
sequence. Its only top-level fields are ShapeId-keyed `external_models`, stable-ID-keyed
`input_adapters`, ordered `protocol_preference`, and opt-in `enabled_integrations`; package, client,
vendor, output, and source selection stay outside that target document. The decoder rejects unknown
or structurally ambiguous values at the boundary.

Installation alone never activates a `PythonIntegration`. Its narrow hooks can preprocess the plan,
decorate the symbol provider, contribute protocol handlers, and configure the completed generation
context. This keeps optional behavior explicit and target-native.

`PythonSymbolProvider` owns Python shape/member names, collision allocation, modules, definition
files, imports, dependencies, and exact Smithy-to-Python type mapping. The built-in protocol
registry handles `aws.protocols#restJson1` and `spitzeisen.protocols#genericRestJson`; an enabled
integration can add another handler without changing Java.

The lowerer interprets the deliberately small `spitzeisen.python` presentation extensions, selects
parameter types and protocol coercions, resolves portable client defaults and registered input
adapters, derives module/class/accessor/constant names, and produces the parameter views and
examples required by the templates. This is also where target capability checks live. The current
Python target rejects unsupported methods, request bindings, event streams, standard Smithy
pagination, or response layouts with a `PythonLoweringError` while leaving the neutral plan valid
for another target.

`python_plan.py` is intentionally renderer-ready. Unlike ServicePlan, its Python types, imports,
coercions, external-versus-generated model choices, and source-layout names are target-specific.
Jinja consumes this plan without inspecting Smithy JSON or reinterpreting Smithy traits. For
generated models, the model coordinator combines the plan's response-shape choices with the
temporary native schema sidecar or original OpenAPI document. Existing Python models are selected
by ShapeId in `PythonSettings.external_models`; stable IDs from the portable `inputAdapter` policy
are resolved through `PythonSettings.input_adapters`.

## Trait ownership

Standard Smithy traits remain authoritative for HTTP bindings, requiredness, defaults,
documentation, constraints, protocols, authentication, errors, pagination, event streams, and
payloads. Spitzeisen adds only policy that cannot be inferred reliably.

Portable traits in `spitzeisen.api` are available to every target:

`spitzeisen.api#result(path, cardinality)`
: Selects the logical response value by a non-empty member path. `cardinality` disambiguates a
  terminal Smithy `Document`; otherwise the compiler derives it from the terminal shape. Every
  operation receives a normalized result policy, including an inferred payload or output-root
  selection.

`spitzeisen.api#notFound(behavior)`
: Selects target-neutral HTTP 404 behavior: `raise` (the default) or `absent`.

`spitzeisen.api#rateLimitCost(units)`
: Records the positive limiter cost acquired for each request.

`spitzeisen.api#pageNumberPagination(pageMember, pageSizeMember, start, step)`
: Names page-number input members for services that cannot use Smithy's cursor-oriented
  `@paginated` trait. `pageSizeMember` is optional; its ceiling comes from the member's standard
  Smithy `@range(max: ...)`. Result selection remains the separate `result` policy.

`spitzeisen.api#sorting(encoding, sortMember, orderMember, separator)`
: Models either `separate` sort/order members or a single `suffix`-encoded member. The separator
  defaults to `.` and is preserved through lowering; allowed values and defaults come from
  standard Smithy enums and `@default`.

`spitzeisen.api#queryEncoding(style, explode, allowReserved)`
: Models generic HTTP collection query serialization. The neutral plan can represent all declared
  styles; each target decides which ones it supports.

`spitzeisen.api#clientDefault(value)`
: Supplies a target-neutral caller-side default distinct from Smithy's wire/model `@default`.

`spitzeisen.api#excludeOperation` and `spitzeisen.api#excludeParameter`
: Omit an operation or input member from generated SDK surfaces.

`spitzeisen.api#inputAdapter(id)`
: Selects a target-neutral adapter by stable identifier. Each target resolves that identifier to
  its own public type, conversion function, imports, and dependencies.

`spitzeisen.protocols#genericRestJson` is a target-neutral protocol declaration for ordinary HTTP
APIs whose document bodies use generic JSON semantics. It prevents such APIs from being mislabeled
as AWS `restJson1`; a target may support either, both, or neither through its protocol registry.

Python-only traits in `spitzeisen.python` are raw ServicePlan extensions interpreted by the Python
lowerer:

`spitzeisen.python#operation(module, accessor, method)`
: Optional Python source-module, aggregate-client accessor, and method-name overrides.

`spitzeisen.python#parameter(name)`
: Optional Python argument-name override.

`spitzeisen.python#modelField(name)`
: Optional Python/Pydantic field-name override, lowered to an alias without changing its modeled
  type.

External model imports, input-adapter implementations, protocol preference, and integration
activation are Python target configuration, not Smithy traits. Standard Smithy
`@externalDocumentation`, `@default`, enum, and `@range` traits remain authoritative instead of
being duplicated in Spitzeisen's trait namespaces.

The definitions are packaged from
`codegen/spitzeisen-smithy-codegen/src/main/smithy/spitzeisen-api.smithy` and
`spitzeisen-python.smithy`, together with the protocol definition in
`spitzeisen-protocols.smithy`, by Smithy's official `smithy-jar` plugin.

## Files and responsibilities

`src/spitzeisen/codegen/openapi.py`
: Owns bounded OpenAPI compatibility projection and the community converter launch.

`src/spitzeisen/codegen/assembly.py`
: Owns official Smithy assembly and loads the custom trait definitions from the bundled JAR.

`codegen/spitzeisen-smithy-codegen/`
: Owns model preparation, ServicePlan compilation, the `spitzeisen-service-plan` plugin, custom
  trait definitions, and the temporary native JSON Schema sidecar. Gradle builds the reproducible
  `spitzeisen-service-plan.jar` installed into the Python package.

`src/spitzeisen/codegen/java_frontend.py`
: Runs the pinned Smithy Build plugin transactionally and reads `service-plan.json` plus
  `model-schema.json`.

`src/spitzeisen/codegen/service_plan.py` and `service_plan_io.py`
: Define and strictly validate the target-neutral Java/Python boundary.

`src/spitzeisen/codegen/python_context.py`, `python_integrations.py`, `python_protocols.py`, and
`python_symbols.py`
: Resolve explicitly enabled Python extensions, protocol behavior, symbols, files, imports, and
dependencies.

`src/spitzeisen/codegen/python_lowering.py` and `python_plan.py`
: Interpret Python traits, enforce Python capabilities, and produce renderer-ready Python facts.

`src/spitzeisen/codegen/generate.py` and `templates/`
: Render regenerated async/sync Python bases plus create-once package, model, operation, and client
  extension modules.

`src/spitzeisen/codegen/inputs.py` and `cli.py`
: Coordinate ingestion, target settings, lowering, model generation, atomic writes, preservation,
  orphan pruning, and drift checks.

## Generated artifacts and validation

Generated response models inherit `SpitzeisenModel`. Declared fields and constraints are validated
by Pydantic; unknown output members are ignored for Smithy-style forward compatibility. Generated
methods remain ergonomic keyword APIs, with optional strict Pydantic validation before request
serialization.

Generation keeps replaceable implementation under `_generated` and scaffolds public package,
model, operation, and client modules once. Re-generation updates implementation without overwriting
client-owned extensions. `spitzeisen-gen check` verifies generated bytes and detects orphaned
replaceable modules.

The verification suite compares direct Smithy Build artifacts with the packaged launcher, exercises
both native Smithy and imported OpenAPI paths, lowers to Python, renders Pydantic and Jinja outputs,
imports generated packages, and checks the committed reproducible JAR and example SDK for drift.
The same ServicePlan boundary can later feed another target, such as Rust, without moving Smithy
semantics into that target's renderer.
