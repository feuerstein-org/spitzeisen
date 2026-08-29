# Java Smithy frontend

## Decision

Spitzeisen's production Smithy semantic frontend is implemented in Java while the SDK renderer,
Pydantic backend, and runtime remain Python.

This is a hybrid implementation boundary, not a Java rewrite of the generated SDK or the runtime:

```text
native Smithy ───────────────────────────────────────┐
                                                    │
OpenAPI ─ compatibility import ─ Smithy overlays ───┤
                                                    ▼
                                      Smithy Build projection
                                                    │
                                                    ▼
                                     Java semantic frontend
                               Model + Smithy knowledge indexes
                                           │              │
                                           ▼              ▼
                                  versioned ClientPlan  JSON Schema
                                           │              │
                                           └──────┬───────┘
                                                  ▼
                                        existing Python backend
                                Jinja + datamodel-code-generator + Ruff
                                                  │
                                                  ▼
                                      ergonomic Python SDK with kwargs
```

The internal `client-plan.json` is generated and ephemeral. It does not replace Smithy traits with a
second user-authored manifest.

## Production verification

`mise run smithy-java-spike` performs three end-to-end production checks.

For native Smithy it:

1. runs the plugin through a standard Smithy Build projection;
2. runs the packaged launcher against the same native source;
3. compares both the versioned `ClientPlan` and official `smithy-jsonschema` result;
4. generates the Pydantic models and all Python SDK modules; and
5. imports the SDK and validates a response with its generated Pydantic model.

For the weather OpenAPI fixture it:

1. performs the existing bounded OpenAPI-to-Smithy import and applies the Smithy overlay;
2. runs the assembled model through direct Smithy Build and the packaged launcher;
3. compares their complete operation plans, including required kwargs, friendly parameter names,
   defaults, documentation, and hidden inputs;
4. retains the original OpenAPI document as the Pydantic backend input; and
5. renders and imports the generated SDK.

The original OpenAPI schema remains authoritative for Pydantic generation because Smithy
translation can discard JSON Schema details. Java owns service and operation semantics in this path,
not the vendor response-schema source.

The policy-complete native fixture additionally compares page-number pagination, range-derived page
sizes, suffix sorting, closed sorting values, custom coercion functions, Literal aliases, client
defaults, required headers and path parameters, hidden parameters, query serialization, and Pydantic
model-property aliases/type overrides.

## Smithy practices exercised

The frontend uses:

- a Gradle multi-project build and checked-in Gradle wrapper;
- a plugin named and configured through `smithy-build.json`;
- Java SPI discovery through `META-INF/services`;
- custom trait definitions packaged under `META-INF/smithy`;
- typed plugin settings;
- `PluginContext` and `FileManifest` for build integration;
- `TopDownIndex`, `OperationIndex`, and `HttpBindingIndex` rather than JSON-AST traversal;
- Smithy projections and standard model transforms;
- the official `smithy-jsonschema` converter; and
- JUnit contract and SPI tests with compiler warnings treated as errors.

The frontend does not use `DirectedCodegen`. Its output is one aggregate, language-neutral plan
rather than source files organized by shape, so adopting a `SymbolProvider`, `SymbolWriter`, and all
shape directives would add abstractions without removing work. If Java later takes ownership of
Python source rendering, `DirectedCodegen` becomes the appropriate next step.

## Runtime and distribution boundary

Gradle produces a timestamp-free, reproducibly ordered `spitzeisen-codegen.jar`. The development
task `mise run install-codegen-frontend` tests the Java module and copies that artifact into the
Python package. Wheels and source distributions include the same JAR.

After OpenAPI import (when needed) and official Smithy assembly, `codegen/java_frontend.py` creates a
temporary Smithy Build projection, launches the packaged plugin with Smithy 1.72.0 and
`smithy-jsonschema` 1.72.0 through Coursier, and reads `client-plan.json` plus `model-schema.json`.
The artifacts disappear with the transaction. Python only deserializes the versioned plan and
renders the established kwargs-based SDK.

The old Python JSON-AST parser, intermediate representation, trait reader, policy compiler, and
standalone Python-to-Java JSON Schema bridge were deleted. Semantic behavior is covered where it is
implemented: JUnit exercises the semantic compiler and its validation failures, while Python tests
consume fixed versioned plans to exercise rendering without reconstructing Smithy policy.
