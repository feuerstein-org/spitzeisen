# Code-generation architecture

Spitzeisen treats code generation as a compiler, the compiler has three deliberate boundaries:

```text
OpenAPI + optional Overlay       manifest.yaml
            │                         │
            ▼                         │
  validated document                  │
  + safe $ref resolver                │
            │                         │
            ▼                         │
  protocol-neutral IR ────────────────┘
            │
            ▼
  typed SDK generation plan
        ┌───┴────────────┐
        ▼                ▼
  Python renderers   Pydantic model backend
        │                │
        └───────┬────────┘
                ▼
    generated async/sync SDK
```

## Files and responsibilities

`diagnostics.py`
: Defines source locations, diagnostics, and `CodegenError`. Compiler failures carry a URI and
  JSON Pointer, so an error identifies the vendor input that caused it.

`document.py`
: Loads JSON or YAML safely, selects OpenAPI 3.0/3.1/3.2 validation, and exposes lazy raw/resolved nodes. Local references may only read files under the configured spec directory. HTTP references are rejected due to safety concerns.

`ir.py`
: Contains the immutable, generator-neutral intermediate representation: operations, parameters, request bodies, responses, status patterns, media types, schema types, and stable reference identities. It describes protocol facts only.

`lower.py`
: Lowers a validated document into the IR. This is where OpenAPI rules are normalized once:
  operation-level parameters replace path-level declarations with the same `(name, in)` identity,
  path parameters follow placeholder order, response status ranges are parsed, 3.0 `nullable` is
  normalized, and references retain canonical identities instead of being recursively expanded.

`manifest.py`
: Defines SDK policy OpenAPI cannot express: the public method/model names, scalar rate-limit cost,
  pagination strategy, not-found behavior, page-size and sorting controls, public parameter names,
  and vendor-specific coercion hooks. Pydantic rejects unknown or contradictory settings early.

`policy.py`
: Joins `DocumentIR` and `Manifest` into an immutable `ClientPlan`. It selects operations, detects
  spec/manifest drift, derives precise Python arguments, validates serializer support, and computes
  imports and helper calls. This is the only layer that knows both OpenAPI and Spitzeisen SDK policy.

`generate.py`
: Renders a `ClientPlan`. It owns the Jinja environment, formatting, output paths, public/generated
  class split, and model export discovery. Its inputs are plans, never raw OpenAPI values.

`cli.py`
: Orchestrates one complete build transaction. `spitzeisen-gen generate` applies an overlay in a
  temporary file, validates and compiles the document, runs the model backend, renders both client
  surfaces, atomically replaces generated files, preserves public extension modules, and removes
  obsolete files only from replaceable generated directories. `spitzeisen-gen check` performs the
  same clean build in memory and compares every generated artifact, including models.

`templates/endpoint.py.jinja` and `templates/endpoint_single.py.jinja`
: Render collection and single-object endpoint bases. Each template is evaluated once for async and
  once for sync because code and documentation differ between the two public surfaces.

`templates/endpoint_public.py.jinja`
: Scaffolds the public endpoint subclass once. SDK authors may customize this file; regeneration
  updates its generated base without overwriting the subclass.

`templates/client.py.jinja` and `templates/client_public.py.jinja`
: Render aggregate client wiring and its create-once public subclass. Every endpoint shares the same
  config and participates in the aggregate context-manager lifecycle.

`templates/model_public.py.jinja` and `templates/model_exports.py.jinja`
: Scaffold response-model subclasses and maintain the public model facade. The storage module remains
  replaceable while callers and endpoint annotations use stable public imports.

## Model backend

The operation compiler is owned by Spitzeisen. Schema storage is currently delegated to
`datamodel-code-generator`, invoked through its documented CLI. The backend receives only selected
operations, plus manifest aliases and type overrides. This boundary is intentional: a future model
backend can replace it without changing document loading, operation lowering, policy compilation,
endpoint templates, or the generated public API.

The raw validated document is retained for this backend because a mature JSON Schema model generator
supports more schema vocabulary than Spitzeisen needs to interpret for endpoint signatures. The
neutral IR remains the source of truth for operation behavior.

## Why not use openapi-python-client's internal IR?

`openapi-python-client` is an excellent complete generator, but its internal property objects encode
that project's generated architecture: Jinja template names, import objects, `Unset` behavior, and
response unions. Depending on those internal types would couple Spitzeisen's public API to upstream
implementation details and reintroduce the broad response types this project deliberately avoids.

We did adopt small, well-tested algorithms where the behavior is an OpenAPI rule rather than an
architectural choice. Parameter precedence and response-status ordering were adapted and rewritten
against Spitzeisen's neutral IR. Their provenance and MIT license are recorded in
`THIRD_PARTY_NOTICES.md` and `licenses/openapi-python-client.txt`.

## Extending the compiler

Adding a protocol feature follows the boundaries above:

1. Preserve and normalize it in `ir.py` and `lower.py` with dialect-specific tests.
2. Decide its SDK policy and supported combinations in `manifest.py` and `policy.py`.
3. Add runtime support.
4. Render the already-compiled plan; do not parse OpenAPI in Jinja or `generate.py`.

For example, POST support is already representable in the frontend. Completing it requires request
body policy, runtime request dispatch, and templates. Until then, selecting a POST operation produces
a source-located `generator.http-method` diagnostic rather than a subtly incorrect GET request.
