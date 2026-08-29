# Spitzeisen Smithy codegen

This Gradle build contains Spitzeisen's production Smithy semantic frontend. It follows Smithy's
standard plugin layout while the project continues to expose a Python runtime and Python SDK
renderer.

`spitzeisen-smithy-codegen`
: A Java SPI `SmithyBuildPlugin` named `spitzeisen-python-client-codegen`. It consumes Smithy's
  validated semantic `Model`, uses the standard knowledge indexes, and writes a versioned
  `client-plan.json` plus a response-model `model-schema.json` through Smithy's `FileManifest`.

`spitzeisen-smithy-codegen-test`
: A normal Smithy Build project that exercises plugin discovery, projections, transforms, bundled
  custom trait definitions, native-weather generation, and the full custom policy surface.

The Gradle wrapper pins the build tool. Java dependencies are pinned in `gradle/libs.versions.toml`
and deliberately match the Smithy version used by the existing Python orchestrator.

Run the Java unit and Smithy integration tests:

```console
./codegen/gradlew -p codegen build
```

Build and copy the reproducible runtime JAR into the Python package:

```console
mise run install-codegen-frontend
```

Run the complete frontend verification from the repository root:

```console
mise run smithy-java-spike
```

That task checks native Smithy, the real weather OpenAPI fixture, and a policy-complete Smithy
fixture through both direct Smithy Build and the packaged launcher. It renders Python and Pydantic
modules and imports the generated packages.

The Python CLI launches this plugin through pinned Smithy Build coordinates and deserializes its
versioned plan. The generated JSON plan is an internal temporary artifact, not a user-maintained
manifest; Smithy models and traits remain the source of truth.
