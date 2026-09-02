# Java Smithy frontend

## Decision

Spitzeisen's production Smithy semantic frontend is implemented in Java. It stops at a
runtime-neutral ServicePlan; Python owns its symbol model, protocol implementation, capability
checks, renderer plan, Pydantic backend, and runtime.

```text
native Smithy ───────────────────────────────────────┐
                                                    │
OpenAPI ─ compatibility import ─ Smithy overlays ───┤
                                                    ▼
                                      Smithy Build projection
                                                    │
                                                    ▼
                                   Java model preparation
                                  + Smithy knowledge indexes
                                           │              │
                                           ▼              ▼
                                  service-plan.json  model-schema.json
                                           │          temporary sidecar
                                           ▼              │
                                strict Python ServicePlan │
                                           ▼              │
                         context + integrations + symbols │
                                           ▼              │
                                   protocol selection     │
                                           ▼              │
                                    Python lowering       │
                                           ▼              │
                                      PythonPlan         │
                                           └──────┬───────┘
                                                  ▼
                                Jinja + datamodel-code-generator + Ruff
                                                  │
                                                  ▼
                                      ergonomic Python SDK with kwargs
```

The internal `service-plan.json` is generated and ephemeral. It is a flat, unversioned object with
`service`, ShapeId-keyed `operations`, ShapeId-keyed `shapes`, and `extensions`. It does not
replace Smithy traits with a second user-authored manifest.

## Java boundary

The Java SPI class is
`org.feuerstein.spitzeisen.codegen.SpitzeisenServicePlanPlugin`; its Smithy Build name is
`spitzeisen-service-plan`. Plugin settings accept only an optional service ShapeId. Python package,
client, vendor, integration, and protocol-preference settings remain entirely outside Java.

The frontend first applies the ordinary service-codegen preparation sequence: service errors are
copied to operations, mixins are flattened and removed, and operations receive dedicated input and
output structures. `ServicePlanCompiler` then uses Smithy's semantic APIs rather than traversing the
JSON AST:

- `TopDownIndex` and `OperationIndex` resolve the service operation closure and modeled errors;
- `HttpBindingIndex` resolves request, response, and error bindings;
- `ServiceIndex` resolves protocols and effective auth schemes;
- `NullableIndex` distinguishes requiredness from client nullability;
- `PaginatedIndex` resolves standard Smithy pagination member paths;
- `EventStreamIndex` finds input and output streams;
- `TopologicalIndex` marks recursive shapes; and
- `Walker` builds the exact transitive data-shape graph.

ServicePlan preserves exact Smithy kinds, ShapeIds, members, enum names and values, defaults with
presence distinct from JSON null, constraints, recursive relationships, protocols, auth, errors,
event streams, and effective HTTP bindings. Portable Spitzeisen policy is normalized under each
node's `policies`; all `spitzeisen.python` traits are copied as raw target extensions. The Java
compiler contains no Python keyword lists, name allocation, annotations, source fragments,
coercion calls, Pydantic options, or renderer capability gates.

Consequently, a valid POST operation, request body, standard `@paginated` operation, or event
stream can cross the Java boundary even though today's Python lowerer rejects it. This distinction
allows a future target to support those semantics without forking Smithy compilation.

The plugin also emits `model-schema.json` through official `smithy-jsonschema`, scoped to neutral
response result roots. This sidecar keeps the existing native Pydantic backend working while
ServicePlan becomes the authoritative target-neutral semantic boundary. Imported OpenAPI still
uses the original vendor document for model generation because the Smithy translation can discard
JSON Schema detail.

## Python target boundary

`service_plan_io.py` strictly loads the unversioned document. `lower_service_plan` creates a
`PythonGenerationContext` from `PythonSettings`, a `PythonSymbolProvider`, one selected
`PythonProtocol`, and explicitly enabled `PythonIntegration` instances.

For command-line generation, `--python-settings` supplies a strict JSON document containing only
ShapeId-keyed external models, stable-ID-keyed input adapters, protocol preference, and explicitly
enabled integrations. Package and client identity remain command arguments, and no target setting
enters the Java plugin configuration or ServicePlan.

Integrations are opt-in by name. They may preprocess the plan, decorate the symbol provider,
contribute protocols, or configure the resolved context; installation by itself has no effect.
`PythonSymbolProvider` owns target names, collision allocation, source files, imports,
dependencies, and Smithy-to-Python types. `PythonProtocolRegistry` selects an installed handler
for a protocol declared by the service.

Only after those choices does lowering interpret `spitzeisen.python` extensions and produce the
renderer-ready `PythonPlan`. Unsupported Python methods, bindings, pagination, event streams,
or response shapes fail here with target-specific diagnostics. Jinja and the model backend consume
that Python plan and do not inspect raw Smithy.

## Trait split

Portable traits are defined in `spitzeisen.api`:

- `result(path, cardinality)` selects the logical response value; Java normalizes an inferred
  result for every operation when the trait is absent;
- `notFound(behavior)` chooses `raise` or `absent` handling for HTTP 404;
- `rateLimitCost(units)` records the positive per-request limiter cost;
- `pageNumberPagination(pageMember, pageSizeMember, start, step)` models non-cursor pagination;
- `sorting(encoding, sortMember, orderMember, separator)` models separate or suffix encoding;
- `queryEncoding(style, explode, allowReserved)` models generic HTTP query serialization;
- `clientDefault(value)` supplies a caller-side default distinct from Smithy's model default;
- `excludeOperation` and `excludeParameter` remove SDK surface elements; and
- `inputAdapter(id)` selects a stable, target-neutral adapter identifier.

Python-only traits are defined in `spitzeisen.python`:

- `operation(module, accessor, method)` controls only Python source presentation;
- `parameter(name)` overrides only a Python argument name; and
- `modelField(name)` overrides only a Python/Pydantic field name.

Smithy's standard `@externalDocumentation`, `@default`, enum, and `@range` traits remain
authoritative. External Python model imports and input-adapter functions/types are target
configuration keyed by ShapeId and adapter ID, respectively; they are not embedded in Smithy.

`spitzeisen.protocols#genericRestJson` is separately packaged as a target-neutral Smithy protocol
definition for ordinary HTTP APIs with generic JSON document bodies. The Python registry currently
supports it and `aws.protocols#restJson1`; Java preserves any declared protocol and does not choose
one on a target's behalf.

Smithy's assembler validates these definitions and their selectors. Java normalizes only portable
policy and preserves Python nodes and declared protocols without target-specific interpretation.

## Production verification

`mise run smithy-java-spike` exercises the direct Smithy Build projection and the packaged Python
launcher.

For native Smithy it:

1. emits `service-plan.json` and `model-schema.json` through direct Smithy Build;
2. emits the same artifacts through the bundled launcher;
3. compares their complete semantic plans and JSON Schema sidecars;
4. lowers ServicePlan into a `PythonPlan`;
5. generates Pydantic and async/sync SDK modules; and
6. imports the generated package and validates a modeled response.

For the weather OpenAPI fixture it:

1. performs the bounded OpenAPI-to-Smithy import and applies the overlay;
2. compares direct and packaged ServicePlans;
3. retains the original OpenAPI document as the Pydantic input;
4. lowers and renders the Python SDK; and
5. imports the generated package.

The policy fixture covers portable page-number pagination, sorting, result selection, exclusion,
range-derived page sizes, query serialization, client defaults, and adapter IDs, alongside the
minimal Python naming overrides. The suite also checks SPI discovery, service-only settings, exact
shape kinds and bindings, target capability failures in Python, reproducible JAR bytes, and
generated-example drift.

## Smithy and distribution practices

The frontend uses a Gradle multi-project build and checked-in wrapper, Java SPI discovery through
`META-INF/services`, custom traits packaged under `META-INF/smithy`, `PluginContext` and
`FileManifest`, official Smithy model preparation/indexes, dependency locking, checksum
verification, formatting, checkstyle, and JUnit tests with compiler warnings treated as errors.

The Java frontend does not use `DirectedCodegen`: it emits one aggregate semantic artifact rather
than target source files. Target concepts similar to a symbol provider live in Python, where they
can be decorated by Python integrations without contaminating ServicePlan.

Gradle produces a timestamp-free, reproducibly ordered `spitzeisen-service-plan.jar`. The
`install-codegen-frontend` task tests the Java module and copies that artifact into the Python
package; wheels and source distributions include the same JAR and trait definitions.

After assembly, `src/spitzeisen/codegen/java_frontend.py` creates a temporary Smithy Build
projection, launches the bundled plugin with the pinned Smithy CLI and `smithy-jsonschema`, and
reads `spitzeisen-service-plan/service-plan.json` plus `model-schema.json`. The temporary
artifacts disappear after the transaction. Generated Python implementation modules are verified
for drift, while create-once public extension modules remain owned by the generated SDK.
