# Code-generation architecture

Spitzeisen consumes one assembled Smithy 2.0 model, even when a vendor publishes only OpenAPI:

```text
vendor OpenAPI ── compatibility projection ── smithy-translate 0.7.8
      │                                         │
      │                                         ▼
      │                                  converted Smithy JSON
      │                                         │
      │             local .smithy overlays ─────┤
      │                                         ▼
      │                              official Smithy assembler
      │                                         │
      │                                  Smithy 2.0 JSON AST
      │                                         │
      │                                  ParsedSmithy frontend
      │                                         ▼
      │                                  typed ClientPlan
      │                                    │          │
      │                                    ▼          ▼
      │                              Jinja clients  public seams
      │
      └──────────────────────────────► datamodel-code-generator
                                             │
                                             ▼
                                      Pydantic models
```

The original OpenAPI schema deliberately remains the Pydantic backend's input. Translation can
lose JSON Schema details, while `datamodel-code-generator` already handles those details well.
Operations, HTTP bindings, requiredness, constraints, documentation, pagination and SDK
customizations reach the Python frontend only through the assembled Smithy model.

There is no parallel Spitzeisen policy document. A local Smithy overlay uses standard traits and
Spitzeisen's small custom trait vocabulary to add information a mechanical OpenAPI import cannot
infer. Smithy's assembler validates the overlay, trait selectors and referenced shape IDs. A vendor
rename therefore fails assembly instead of silently dropping an SDK customization.

Package name, aggregate client class and output directory remain command-line build settings because
they describe a Python artifact rather than the service. Base URLs, credentials, strict input mode
and unknown-response-member handling remain runtime policy.

## OpenAPI importer and Smithy assembly

`codegen/openapi.py` launches the pinned Maven artifact
`com.disneystreaming.smithy:smithytranslate-cli_2.13:0.7.8`. The converter's OpenAPI 3.1 path emits
unsupported placeholders for ordinary scalar schemas, so Spitzeisen projects the compatible 3.1
subset into 3.0.3 first. Nullable scalar unions, `const`, and numeric exclusive bounds have explicit
mappings. Genuinely 3.1-only JSON Schema features fail with their document path.

`codegen/assembly.py` then launches the official Smithy CLI pinned to 1.72.0, matching the Smithy
libraries used by the converter. It assembles:

- the converted JSON AST;
- Spitzeisen's custom trait definitions;
- every repeatable `--overlay` input.

Both tools are launched through Coursier when available and can be replaced with explicit commands
through `SPITZEISEN_SMITHYTRANSLATE` and `SPITZEISEN_SMITHY`. Java and Coursier are pinned in this
repository's `mise.toml`.

`spitzeisen-gen import-openapi` writes the fully assembled model consumed by the frontend. Normal
`generate` and `check` commands perform the same conversion and assembly in temporary directories.

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
: Loads OpenAPI, converts and assembles Smithy, parses the completed model and returns one successful
  `BuildInputs` value.

`codegen/openapi.py`
: Owns the bounded OpenAPI compatibility projection and community converter launch.

`codegen/assembly.py` and `codegen/smithy/spitzeisen.smithy`
: Own official model assembly and the custom trait contract.

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
