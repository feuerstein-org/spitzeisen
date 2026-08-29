# Code-generation architecture

Spitzeisen consumes one assembled Smithy 2.0 model. OpenAPI is an optional ingestion path:

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
      │                                      Smithy 2.0 JSON AST
      │                                               │
      │                                               ▼
      │                                  Java Smithy Build plugin
      │                                semantic Model + knowledge indexes
      │                                       │             │
      │                                       ▼             ▼
      │                              versioned ClientPlan  JSON Schema
      │                                  │          │       │
      │                                  ▼          ▼       ▼
      │                            Jinja clients  public  Pydantic models
      │
      └──────────────────────────────────────────► datamodel-code-generator
```

For native input, the official `smithy-jsonschema` library converts only the generated response
shape closures. It preserves standard Smithy requiredness, constraints, documentation, defaults,
enums, collections, unions and references without making Spitzeisen maintain those mappings.
`datamodel-code-generator` then produces the same Pydantic base classes used by OpenAPI projects.

The original OpenAPI schema deliberately remains the Pydantic backend's input. Translation can lose
JSON Schema details, while `datamodel-code-generator` already handles those details well. Operations,
HTTP bindings, requiredness, constraints, documentation, pagination and SDK customizations still
reach the Java semantic frontend only through the assembled Smithy model.

There is no parallel Spitzeisen policy document. A local Smithy overlay uses standard traits and
Spitzeisen's small custom trait vocabulary to add information a mechanical OpenAPI import cannot
infer. Smithy's assembler validates the overlay, trait selectors and referenced shape IDs. A vendor
rename therefore fails assembly instead of silently dropping an SDK customization.

Package name, aggregate client class and output directory remain command-line build settings because
they describe a Python artifact rather than the service. Base URLs, credentials, strict input mode
and unknown-response-member handling remain runtime policy.

## Model inputs and Smithy assembly

`spitzeisen-gen generate --smithy model/service.smithy` is the direct path. `--smithy` may be
repeated and may be combined with repeatable `--overlay` sources. Native input never invokes an
OpenAPI converter.

`codegen/openapi.py` launches the pinned Maven artifact
`com.disneystreaming.smithy:smithytranslate-cli_2.13:0.7.8`. The converter's OpenAPI 3.1 path emits
unsupported placeholders for ordinary scalar schemas, so Spitzeisen projects the compatible 3.1
subset into 3.0.3 first. Nullable scalar unions, `const`, and numeric exclusive bounds have explicit
mappings. Genuinely 3.1-only JSON Schema features fail with their document path.

`codegen/assembly.py` launches the official Smithy CLI pinned to 1.72.0. It assembles:

- native sources or the converted JSON AST;
- Spitzeisen's custom trait definitions;
- every repeatable `--overlay` input.

Both ingestion tools are launched through Coursier when available and can be replaced with explicit
commands through `SPITZEISEN_SMITHYTRANSLATE` and `SPITZEISEN_SMITHY`. Java and Coursier are pinned
in this repository's `mise.toml`.

`spitzeisen-gen import-openapi` writes the fully assembled model consumed by the frontend. Normal
`generate` and `check` commands perform the same conversion and assembly in temporary directories.

For native model generation, the Java plugin invokes the official
`software.amazon.smithy:smithy-jsonschema:1.72.0` library directly. It scopes conversion to response
shapes selected by `ClientPlan`; Spitzeisen does not implement schema semantics itself.

## Traits

Standard Smithy traits remain authoritative for `@http`, HTTP bindings, `@required`, `@default`,
documentation, ranges, enums, authentication, errors and output payloads. Spitzeisen defines only
the codegen-specific information that Smithy's prelude cannot express:

`spitzeisen.api#sdkOperation`
: Optional operation/module names, response override, handwritten-model switch, ambiguous response
  cardinality, rate-limit cost, not-found behavior and top-level result path.

`spitzeisen.api#pythonParameter`
: Friendly Python name, generic HTTP query serialization, coercion, client-owned Literal or
  function, annotation escape hatch and an SDK-only client default.

`spitzeisen.api#hidden`
: Omits an imported operation or input member from the generated Python surface.

`spitzeisen.api#pageNumberPagination`
: Describes page-number APIs that cannot use Smithy's cursor-oriented standard `@paginated` trait.

`spitzeisen.api#sorting`
: Describes separate or suffix-based vendor sorting controls.

`spitzeisen.api#modelProperty`
: Supplies the existing Pydantic backend's property aliases and type overrides.

The definitions are shipped in `codegen/smithy/spitzeisen.smithy`. Overlays apply them externally,
so converted files remain disposable and vendor updates are easy to review.

## Smithy frontend

The production `spitzeisen-python-client-codegen` Smithy Build plugin consumes Smithy's semantic
`Model`. `TopDownIndex`, `OperationIndex`, and `HttpBindingIndex` own service closure, response
inference, and HTTP bindings; standard typed traits own requiredness, defaults, documentation,
ranges, and names. The plugin reads Spitzeisen's custom traits and emits a versioned, immutable
`ClientPlan` plus the native response JSON Schema.

`codegen/java_frontend.py` runs the bundled plugin JAR with pinned Smithy dependencies and loads both
artifacts transactionally. Python never reparses the Smithy JSON AST during production generation.
Native Smithy, the real weather OpenAPI fixture, and a policy-complete fixture exercise this boundary
through direct Smithy Build and the packaged CLI launcher. See
[`java-smithy-frontend-spike.md`](java-smithy-frontend-spike.md).

Spitzeisen currently implements a constrained generic HTTP/JSON profile rather than claiming that
arbitrary vendor APIs use AWS `restJson1`. Query collections use the profile default unless an
overlay supplies explicit serialization. Cursor-based standard `@paginated`, nested result paths,
non-GET operations and request bodies currently fail with explicit errors.

## Files and responsibilities

`codegen/inputs.py`
: Selects native Smithy or OpenAPI, assembles the completed model and returns one successful
  `BuildInputs` value with the appropriate Pydantic backend document.

`codegen/openapi.py`
: Owns the bounded OpenAPI compatibility projection and community converter launch.

`codegen/assembly.py` and `codegen/smithy/spitzeisen.smithy`
: Own official model assembly and the custom trait contract.

`codegen/java_frontend.py` and the bundled `codegen/smithy/spitzeisen-codegen.jar`
: Launch the pinned Smithy Build plugin and deserialize its versioned outputs.

`codegen/spitzeisen-smithy-codegen/`
: Owns semantic Smithy compilation and official native JSON Schema projection in Java. Gradle builds
  a reproducible JAR and `mise run install-codegen-frontend` installs it into the Python package.

`codegen/plan.py` and `codegen/plan_io.py`
: Define and validate the small versioned renderer contract emitted by Java. They contain no Smithy
  parsing or policy compilation.

`codegen/generate.py` and `codegen/templates/`
: Render regenerated async/sync bases, a create-once package-root export facade, and create-once
  public extension modules. A completely empty output directory therefore becomes an importable
  SDK package in one generation transaction.

`codegen/cli.py`
: Coordinates transactional generation, inspectable imports, atomic writes, drift checks, pruning
  and the replaceable Pydantic backend.

## Validation policy

Generated response models inherit `SpitzeisenModel`. Declared fields and constraints are validated
by Pydantic, while unknown output members are ignored deliberately for Smithy-style forward
compatibility.

Generated operations retain ergonomic keyword arguments. `strict_inputs=False` is permissive;
setting it to `True` validates every generated argument with Pydantic strict mode before
serialization.
