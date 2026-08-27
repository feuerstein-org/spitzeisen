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
      │                                       │             │
      │                                       │             ▼
      │                                       │    official smithy-jsonschema
      │                                       │             │
      │                                       ▼             ▼
      │                               ParsedSmithy       JSON Schema
      │                                       │             │
      │                                       ▼             ▼
      │                                typed ClientPlan  datamodel-code-generator
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
reach the Python frontend only through the assembled Smithy model.

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

Both tools are launched through Coursier when available and can be replaced with explicit commands
through `SPITZEISEN_SMITHYTRANSLATE` and `SPITZEISEN_SMITHY`. The JSON Schema bridge can likewise be
replaced through `SPITZEISEN_SMITHY_JSONSCHEMA`. Java and Coursier are pinned in this repository's
`mise.toml`.

`spitzeisen-gen import-openapi` writes the fully assembled model consumed by the frontend. Normal
`generate` and `check` commands perform the same conversion and assembly in temporary directories.

For native model generation, `codegen/smithy_jsonschema.py` invokes the official
`software.amazon.smithy:smithy-jsonschema:1.72.0` artifact through a minimal packaged Java source
launcher. Coursier supplies its classpath. The launcher scopes conversion to response shapes selected
by `ClientPlan`; it does not implement schema semantics itself.

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

`codegen/parser/smithy.py` reads the assembled JSON AST. It handles service membership, operation
`@http` traits, query/header/label/payload bindings, required/default/documentation/range/enum/date
semantics, input/output structures and error statuses. It lowers protocol facts into the small
types in `codegen/ir.py`; nothing downstream receives converter objects.

`codegen/traits.py` reads Spitzeisen's custom traits. `codegen/policy.py` selects the requested
service closure, infers response models and cardinality where possible, and compiles everything into
an immutable `ClientPlan` before rendering.

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

`codegen/smithy_jsonschema.py` and `codegen/smithy/SmithyJsonSchema.java`
: Launch Smithy's official JSON Schema converter for native response-model generation.

`codegen/parser/smithy.py`, `codegen/traits.py`, and `codegen/ir.py`
: Parse standard Smithy semantics, parse custom traits, and represent the protocol-neutral type tree.

`codegen/policy.py`
: Compiles a selected Smithy service into the renderer-ready plan.

`codegen/generate.py` and `codegen/templates/`
: Render regenerated async/sync bases and create-once public extension modules.

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
