# Smithy Python client generator

`spitzeisen-smithy-codegen` is a Java Smithy Build plugin named
`spitzeisen-python-client-codegen`. It directly generates Python source using Smithy's Model,
knowledge indexes, SymbolProvider, SymbolWriter, and FileManifest.

The Python CLI launches the packaged plugin and coordinates the existing Pydantic backend.
There is no neutral service plan, Python lowering stage, or Jinja renderer.
See [the architecture](../docs/codegen-architecture.md) for the supported profile, source
ownership, model-name mapping, portable policies, and upstream reuse decisions.

## Build and verify

The checked-in Gradle wrapper and `mise.toml` select the toolchain.

```bash
mise run install-codegen-frontend
mise run verify-codegen
mise run codegen
mise run check-codegen
```

The first task checks the Java generator and direct Smithy Build fixtures, then installs the thin,
reproducible `spitzeisen-python-codegen.jar` into the Python package. `verify-codegen` also executes
real generated native-Smithy and OpenAPI SDKs against an offline HTTP transport.

## Direct Smithy Build use

```json
{
  "version": "1.0",
  "plugins": {
    "spitzeisen-python-client-codegen": {
      "service": "native.weather#WeatherService",
      "package": "weather_sdk",
      "client_name": "WeatherClient",
      "python": {}
    }
  }
}
```

The plugin emits raw Python files under `sdk/`, `manifest.json`, and `model-schema.json`.
These are build intermediates: use `spitzeisen-gen generate` to finalize the complete SDK with
Pydantic models, formatting, and preserved public extensions. Raw Smithy Build output alone is not
a complete installable package.

The Apache-licensed writer adaptation and external dependency notices are documented in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). No upstream generator or runtime tree is vendored.
