# Add initial OpenAPI 3.1 schema translation

> Archived draft from the deferred Smithy experiment. Versions, support status, and validation
> results describe the original experiment; they have not been revalidated for the current runtime.
> See [the deferred design](../smithy-future.md) before resuming this work.

OpenAPI 3.1 scalar schemas currently become empty `error#...` structures because the
converter matches Swagger's specialized 3.0 classes, while the parser represents 3.1
schemas as `JsonSchema`. A string query parameter consequently produces an invalid
Smithy `httpQuery` binding.

This change selects mappings from schema type and format information. A document
containing a string query parameter and string, integer, and boolean response fields
now produces the same Smithy model under OpenAPI 3.0.3 and 3.1.0, with both input and
output validation enabled.

## Changes

- Read both the 3.0 `type` and 3.1 `types` representations without changing the input
  document or mutating parsed models.
- Apply existing primitive/format, collection, object, string enum, reference, `allOf`,
  and `oneOf` mappings to 3.1 schemas.
- Preserve a single type combined with `null` as `alloy#nullable`, independently of
  member requiredness; account for enums that exclude null.
- Map unconstrained schemas to documents and retain 3.1 schema `examples`.
- Combine numeric exclusive and inclusive bounds, rounding integer bounds to permitted
  values. Fix the existing exclusive-maximum check and missing named-blob builder
  exposed by the new coverage.
- Report unsupported 3.1 constraints before selecting a type mapping. Strict output
  validation fails; best-effort conversion retains diagnostic placeholders.
- Document the supported subset and remaining limitations.

## Scope

This is initial OpenAPI 3.1 support within the existing best-effort converter, not full
JSON Schema 2020-12 evaluation. General type unions, non-string enums, `const`, `not`,
`anyOf`, conditional/tuple/containment/dependent/unevaluated constraints, and validation
siblings of `$ref` remain unsupported. Exclusive decimal bounds remain diagnostic
conversions because Smithy cannot represent them exactly.

The existing Swagger parser also rejects boolean-valued entries directly under
`components.schemas`; supported nested boolean schemas and parsed models are handled.
Default-value conversion and webhook operations remain outside this change. No parser
dependency upgrade or input downgrade is required.

## Validation

The regression suite covers scalar formats, nested collections and references,
composition, property and example keys named `const` and `if`, parsed input preservation,
HTTP bindings, nullable enums and required members, integer bounds, and strict versus
best-effort diagnostics.

- OpenAPI suite: 211 tests on each of Scala 2.12.21, 2.13.18, and 3.3.7, including 59
  new tests.
- Compiler-core: 9 tests; JSON Schema: 89 tests on Scala 2.13.18.
- Repository formatting and license-header checks, OpenAPI binary compatibility, and
  documentation example validation all pass.
- Original scalar/HTTP reproduction: CLI succeeds with `--validate-input --validate-output`;
  the 3.1.0 and 3.0.3 JSON outputs are identical and contain no `error#...` shapes.

Useful commands from the repository root:

```sh
./mill --no-server --ticker false 'openapi[2.12.21].test'
./mill --no-server --ticker false 'openapi[2.13.18].test'
./mill --no-server --ticker false 'openapi[3.3.7].test'
./mill --no-server --ticker false 'compiler-core[2.13.18].test'
./mill --no-server --ticker false 'json-schema[2.13.18].test'
./mill --no-server --ticker false __.checkFormat
./mill --no-server --ticker false __.headerCheck
./mill --no-server --ticker false 'openapi[2.13.18].mimaReportBinaryIssues'
./mill --no-server --ticker false readme-validator.validate
```
