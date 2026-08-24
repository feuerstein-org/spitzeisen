# Code-generation architecture

Spitzeisen deliberately follows openapi-python-client until the point where the two projects need
different generated SDKs:

```text
explicit --path or --url             Spitzeisen manifest
          │                                  │
          ▼                                  │
strict source loading                        │
(httpx + ruamel.yaml)                        │
          │                                  │
          ▼                                  │
vendored OpenAPI Pydantic model              │
          │                                  │
          ▼                                  │
ParsedOpenAPI.from_dict()                    │
components → operation collections           │
          │                                  │
          └──────────────┬───────────────────┘
                         ▼
                  typed ClientPlan
                    ┌────┴────┐
                    ▼         ▼
              Jinja output  model backend
                    └────┬────┘
                         ▼
                 async + sync SDK
```

There is intentionally no separate OpenAPI validator, overlay processor, dialect abstraction,
diagnostics graph, document-node graph, general `$ref` resolver, or manifest-only path. Pydantic
hydration is the document-validation boundary. As in upstream, only local component references are
supported by the parser.

## Files and responsibilities

`codegen/exceptions.py`
: Defines the structured exception used for expected fatal failures in Spitzeisen-owned code. The
  exception carries a short heading and optional detail; the CLI formats it once at its outer
  boundary without hiding unexpected programming errors.

`codegen/inputs.py`
: Owns the complete external-input workflow. It loads JSON or YAML mappings, fetches or reads the
  OpenAPI document, validates the manifest, parses `ParsedOpenAPI`, collects recoverable warnings,
  and compiles both inputs into `BuildInputs`. HTTP failures, invalid top-level values, and validation
  errors become `CodegenError` with their original exception preserved as the cause. The rest of the
  CLI receives one successful value instead of coordinating each input phase.

`codegen/schema/`
: An upstream-shaped snapshot of the complete Pydantic OpenAPI object model. `OpenAPI`,
  `PathItem`, `Operation`, `Param`, `Response`, `RequestBody`, `Schema`, and `Reference` are the
  objects the active parser consumes. Provenance and update rules are in
  `vendored-openapi-schema.md`.

`codegen/parser/errors.py`
: Upstream's small `GeneratorError`/`ParseError` data structures, retained for parser compatibility.
  Fatal document hydration now uses Pydantic's exception contract. Recoverable `ParseError` values
  may omit an unsupported operation or response while allowing the rest of a client to be generated;
  `--fail-on-warning` turns those warnings into a non-zero exit.

`codegen/parser/openapi.py`
: The OpenAPI-to-parsed-data boundary. Its workflow mirrors upstream's
  `ParsedOpenAPI.from_dict()`: hydrate `OpenAPI`, build component registries, traverse paths into
  tag collections, parse operation params before Path Item params, sort path params,
  resolve request/response component references, order response patterns, and collect warnings.

  Its output is intentionally Spitzeisen-specific after that workflow. `ParsedOperation` retains the
  protocol facts our policy needs instead of upstream's `attrs`, `Unset`, import, and response-union
  objects. The manifest's separate `ManifestOperation` contains SDK policy; `OperationPlan` is the
  merged renderer-ready result.

`codegen/ir.py`
: The small schema-type IR used inside parsed operations: primitives, literals, arrays, objects,
  references, unions, intersections, media types, params, request bodies, and responses. It no
  longer duplicates the complete document or operation hierarchy already represented by upstream's
  Pydantic/parser architecture.

`codegen/manifest.py`
: Defines SDK policy OpenAPI cannot express: public method/model names, scalar rate-limit cost,
  pagination strategy, not-found behavior, page-size and sorting controls, public param names,
  client defaults, and vendor-specific coercion hooks. `required` remains the API/wire
  requirement; `client_default` only changes whether the generated caller must supply a value.
  OpenAPI remains authoritative for operations and params.

`codegen/policy.py`
: Joins `ParsedOpenAPI` and `Manifest` into an immutable `ClientPlan`. It selects parsed operations,
  detects manifest/spec drift, derives Python arguments, checks runtime serializer support, and
  computes imports and helper calls. This is the first layer that intentionally diverges from
  openapi-python-client's generated architecture.

`codegen/generate.py`
: Renders a `ClientPlan`. It owns the Jinja environment, formatting, output paths,
  public/generated class split, and model export discovery. Templates receive plans, never raw
  OpenAPI mappings or Pydantic objects.

`codegen/cli.py`
: A Typer interface modeled after upstream. Both `generate` and Spitzeisen's additional `check`
  command require exactly one of `--path`/`--url`, a `--manifest`, and an `--output-path`.
  Generation runs the model backend, renders both surfaces, atomically replaces generated files,
  preserves public extension modules, and removes obsolete files only from generated directories.

`codegen/templates/operation.py.jinja` and `operation_single.py.jinja`
: Render collection and single-object operation bases once for async and once for sync.

`codegen/templates/operation_public.py.jinja`
: Scaffolds the public operation subclass once. SDK authors own this file after creation.

`codegen/templates/client.py.jinja` and `client_public.py.jinja`
: Render aggregate client wiring and its create-once public subclass.

`codegen/templates/model_public.py.jinja` and `model_exports.py.jinja`
: Scaffold response-model subclasses and maintain stable public model imports.

## Why the IR diverges

openapi-python-client's post-parse property objects encode its generated package: `attrs` models,
`Unset`, import objects, per-operation `.sync`/`.async` functions, and broad response unions.
Spitzeisen instead generates conventional typed API classes, paired async/sync surfaces, Pydantic
response validation, and manifest-driven rate-limit cost and pagination. Reusing upstream's final IR
would import the exact API design this project is intended to replace.

The maintained boundary is therefore narrow and explicit:

1. Copy upstream source loading and Pydantic hydration.
2. Mirror its component/operation parsing workflow and local-reference behavior.
3. Lower into Spitzeisen's small protocol representation.
4. Apply Spitzeisen policy and render its public SDK design.

## Model backend

Schema model emission remains delegated to `datamodel-code-generator`. It receives the raw loaded
document pruned to manifest-selected paths, plus manifest aliases and type overrides. The parser IR
is authoritative for operation signatures and wire behavior; the mature backend handles the wider
JSON Schema vocabulary needed by response models.

## Extending code generation

When adding a protocol feature, preserve upstream's parsing order and error behavior where
possible. Extend the typed operation/IR representation only for facts Spitzeisen policy needs, then
add policy, runtime support, and rendering. For example, non-GET operations and request bodies are
already parsed; generation rejects them plainly until the runtime and templates can represent them
faithfully.
