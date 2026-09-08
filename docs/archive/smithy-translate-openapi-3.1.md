# OpenAPI 3.1 support: ordinary scalar schemas become placeholder structures

> Archived draft from the deferred Smithy experiment. Versions, support status, and validation
> results describe the original experiment; they have not been revalidated for the current runtime.
> See [the deferred design](../smithy-future.md) before resuming this work.

With smithy-translate 0.7.8, a small OpenAPI 3.1.0 document containing string, integer,
and boolean schemas produces empty `error#...` structures. The same document with only
its `openapi` field changed to `3.0.3` produces the expected Smithy scalar targets.

The [capabilities documentation](https://github.com/disneystreaming/smithy-translate/blob/main/modules/docs/openapi.md#capabilities-and-design)
describes support for OpenAPI 2.x and 3.x and explains that conversion is best effort.
Could this issue track OpenAPI 3.1 support, or be linked to an existing effort? This
reproduction uses ordinary scalar schemas, with no 3.1-specific schema keywords or type unions.

## Environment

- Artifact: `com.disneystreaming.smithy:smithytranslate-cli_2.13:0.7.8`
- Main class: `smithytranslate.cli.Main`
- Coursier: 2.1.24
- Java: Eclipse Temurin 25.0.4.1+1, Linux
- Reproduced directly with the upstream CLI, without a downstream preprocessor or plugin.

## Reproduction

Save this as `openapi.json`:

```json
{
  "openapi": "3.1.0",
  "info": {"title": "Scalar reproduction", "version": "1.0.0"},
  "paths": {
    "/items": {
      "get": {
        "operationId": "getItem",
        "parameters": [
          {"name": "id", "in": "query", "required": true, "schema": {"type": "string"}}
        ],
        "responses": {
          "200": {
            "description": "OK",
            "content": {
              "application/json": {
                "schema": {
                  "type": "object",
                  "properties": {
                    "name": {"type": "string"},
                    "count": {"type": "integer"},
                    "active": {"type": "boolean"}
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

Run:

```sh
mkdir -p out-31
cs launch com.disneystreaming.smithy:smithytranslate-cli_2.13:0.7.8 \
  --main-class smithytranslate.cli.Main -- \
  openapi-to-smithy --input openapi.json --validate-input --json-output out-31
```

For the control, change only `"openapi": "3.1.0"` to `"openapi": "3.0.3"` in the
same file, then run the same command with `out-30` as the output directory.
Keeping the input filename the same also keeps the generated `openapi` namespace the same.

## Actual result

The 3.1.0 run reports `Schema not supported` for each scalar. The diagnostic identifies
the parser objects as `class JsonSchema`, with `type: [string]`, `type: [integer]`, or
`type: [boolean]` respectively.

It writes `out-31/result.json` with these member targets:

| Member | OpenAPI 3.1.0 target | OpenAPI 3.0.3 target |
| --- | --- | --- |
| `openapi#GetItemInput$id` | `error#Id` | `smithy.api#String` |
| `openapi#Body$name` | `error#Name` | `smithy.api#String` |
| `openapi#Body$count` | `error#Count` | `smithy.api#Integer` |
| `openapi#Body$active` | `error#Active` | `smithy.api#Boolean` |

All four `error#...` targets are structures with empty `members` and a
`smithytranslate#errorMessage` trait containing the unsupported-schema diagnostic.
Consequently, the query member targets a structure and Smithy validation reports that
`httpQuery` cannot be applied to `openapi#GetItemInput$id` (`TraitTarget`).

The command above exits with status 0 and writes the partial model while reporting these
diagnostics. It uses `--validate-input`, without `--validate-output`; I understand that
partial output is part of the documented best-effort behavior. The problem here is the
failure to translate these scalar types.

The 3.0.3 control exits with status 0, produces the scalar targets shown above, and emits
no unsupported-schema or Smithy validation diagnostics.

## Expected result / support status

For this common subset, OpenAPI 3.1.0 should produce the same scalar targets as 3.0.3.
The query binding should target a string, and the response fields should retain their
string, integer, and boolean types.

If OpenAPI 3.1 is currently outside the supported scope, could the documentation make
the supported versions explicit and link to a tracking issue for 3.1? A clear
unsupported-version diagnostic would also make the current limitation easier to identify.

We are holding our downstream importer to OpenAPI 3.0.x until upstream support is
available. We would prefer to consume a release with 3.1 regression coverage rather than
maintain a separate schema downgrade pass. The version-only change above is a control
for this deliberately small example, not a proposed conversion strategy for arbitrary
3.1 documents.
