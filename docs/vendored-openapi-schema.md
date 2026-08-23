# Vendored OpenAPI schema model

Spitzeisen vendors `openapi_python_client/schema` from openapi-python-client commit
`ee9a8c435b92e7425a0b68be2d42bae977bf7526` at `src/spitzeisen/codegen/schema`.

The initial import is intentionally byte-for-byte identical to that upstream directory. This gives
Spitzeisen the same Pydantic representation of OpenAPI objects, including `OpenAPI`, `Operation`,
`PathItem`, `Parameter`, `Response`, `Reference`, and `Schema`, without depending on
openapi-python-client's private Python package at runtime.

## Provenance

openapi-python-client vendors and patches `openapi-schema-pydantic` commit
`0836b429086917feeb973de3367a7ac4c2b3a665`. Both projects use the MIT License. Their notices and
license texts are preserved in `THIRD_PARTY_NOTICES.md`, `licenses/`, and the vendored directory.

The four upstream `tests/test_schema` modules are mirrored under `tests/test_openapi_schema`. Their
only source change is the import prefix needed for Spitzeisen's package namespace.

## Modification policy

The vendored package is excluded from Spitzeisen's Ruff and Pyright rules so automated fixes cannot
silently diverge it from upstream. Behavioral changes should be recorded as explicit patches, tested
separately, and documented here with their reason. Upstream updates should begin with another exact
directory import and a comparison against the previously pinned commit.

The snapshot is the active document model. `GeneratorData.from_dict()` hydrates `OpenAPI` directly,
then the parser follows upstream's component-registry and endpoint-collection workflow before
lowering the facts needed by Spitzeisen policy.
