# Spitzeisen Smithy codegen

This Gradle build contains Spitzeisen's production target-neutral Smithy frontend. Python remains
the first target runtime and owns its generation context, symbol provider, protocol handlers,
integrations, lowering, source renderer, and Pydantic backend.

`spitzeisen-smithy-codegen`
: Provides the Java SPI class `SpitzeisenServicePlanPlugin`, registered with Smithy Build as
  `spitzeisen-service-plan`. It accepts only an optional service ShapeId, prepares Smithy's semantic
  `Model`, uses official knowledge indexes, and writes flat, unversioned `service-plan.json` plus
  a temporary native-model `model-schema.json` sidecar through `FileManifest`.

`spitzeisen-smithy-codegen-test`
: A normal Smithy Build project that exercises plugin discovery, projections, preparation
  transforms, bundled traits, native-weather generation, exact service semantics, and the custom
  policy surface.

The ServicePlan root contains `service`, ShapeId-keyed `operations`, ShapeId-keyed `shapes`, and
`extensions`. It preserves exact Smithy types, relationships, defaults, constraints, protocols,
auth, errors, event streams, pagination, and HTTP bindings. Portable `spitzeisen.api` traits are
normalized as policies; raw `spitzeisen.python` traits are carried as extensions. Java performs no
Python naming, type mapping, coercion rendering, Pydantic configuration, or Python capability
checks.

The bundled traits are split between:

- `spitzeisen-api.smithy`: portable `result`, `notFound`, `rateLimitCost`,
  `pageNumberPagination`, `sorting`, `queryEncoding`, `clientDefault`, `excludeOperation`,
  `excludeParameter`, and `inputAdapter` policy;
- `spitzeisen-python.smithy`: minimal Python `operation`, `parameter`, and `modelField` naming and
  layout overrides; and
- `spitzeisen-protocols.smithy`: the target-neutral `genericRestJson` protocol for ordinary HTTP
  APIs with generic JSON document bodies.

Standard Smithy traits remain authoritative for documentation, defaults, enums, ranges, HTTP,
pagination, auth, errors, and event streams. Existing Python models and adapter implementations are
selected through the CLI's strict `--python-settings` JSON document and decoded into
`PythonSettings`, rather than encoded as target-specific Smithy type or function strings.

The Python pipeline strictly loads ServicePlan, explicitly enables integrations, builds a
`PythonGenerationContext`, resolves its `PythonSymbolProvider` and declared protocol handler,
then calls `lower_service_plan` to produce a renderer-ready `PythonPlan`. Unsupported
Python features fail in that lowerer, leaving the neutral Java artifact usable by another target.
The built-in registry handles `spitzeisen.protocols#genericRestJson` and
`aws.protocols#restJson1`.

The Gradle wrapper pins the build tool. Shared Java and Smithy versions live in
`src/spitzeisen/codegen/smithy/toolchain.properties`, which Gradle and the Python launcher both
read. Lockfiles and SHA-256 verification metadata make the build dependency graph reproducible and
verified.

Run all Python and Java lint checks from the repository root:

```console
mise run lint
```

`mise run lint-fix` also applies the Java formatter. To format only the Java frontend:

```console
./codegen/gradlew -p codegen :spitzeisen-smithy-codegen:spotlessApply
```

Run the Java unit and Smithy integration tests:

```console
./codegen/gradlew -p codegen build
```

Build and copy the reproducible `spitzeisen-service-plan.jar` into the Python package:

```console
mise run install-codegen-frontend
```

Run the complete frontend verification:

```console
mise run smithy-java-spike
```

That task compares direct Smithy Build output with the packaged launcher for native Smithy, the real
weather OpenAPI fixture, and a policy-complete fixture. It verifies `service-plan.json`, the
temporary model-schema sidecar, Python lowering, rendered Jinja/Pydantic modules, generated package
imports, and the reproducible bundled artifact.

The ServicePlan and model schema are internal temporary artifacts, not user-maintained manifests.
Smithy models and traits remain the source of truth; generated implementation modules are checked
for drift, and create-once public extension modules remain untouched on regeneration.
