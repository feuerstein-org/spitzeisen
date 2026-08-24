# Vendored OpenAPI schema model

Spitzeisen vendors `openapi_python_client/schema` from openapi-python-client commit `ee9a8c435b92e7425a0b68be2d42bae977bf7526` at `src/spitzeisen/codegen/schema`.

The initial import follows the upstream directory closely. Spitzeisen keeps the same Pydantic representation and parsing workflow for OpenAPI objects, while using concise local `Param` names for the Python-facing classes and fields. OpenAPI's serialized `"parameters"` keys remain unchanged through Pydantic aliases.

## Provenance

openapi-python-client vendors and patches `openapi-schema-pydantic` commit `0836b429086917feeb973de3367a7ac4c2b3a665`. Both projects use the MIT License. Their notices and license texts are preserved in `THIRD_PARTY_NOTICES.md`, `licenses/`, and the vendored directory.

The four upstream `tests/test_schema` modules are mirrored under `tests/test_openapi_schema`. Their only source change is the import prefix needed for Spitzeisen's package namespace.
