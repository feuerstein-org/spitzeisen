# Third-party notices

## openapi-python-client

Spitzeisen vendors the complete `openapi_python_client/schema` package from
openapi-python-client commit `ee9a8c435b92e7425a0b68be2d42bae977bf7526` under
`src/spitzeisen/codegen/schema`. The vendored source is copied byte-for-byte, including its OpenAPI
3.0.3 and 3.1.0 specification snapshots, README, and nested license. Its upstream tests are copied
under `tests/test_openapi_schema`; only their import namespace is changed from
`openapi_python_client.schema` to `spitzeisen.codegen.schema`.

The vendored `openapi_schema_pydantic` subpackage was itself derived by openapi-python-client from
`openapi-schema-pydantic` commit `0836b429086917feeb973de3367a7ac4c2b3a665` and subsequently
patched upstream. Its MIT license is included both with the vendored source and at
`licenses/openapi-schema-pydantic.txt`.

The loading and parsing frontend is also adapted from the same pinned openapi-python-client commit,
specifically:

- JSON/YAML source selection, URL fetching, and safe `ruamel.yaml` loading;
- `GeneratorError`, `ParseError`, `PropertyError`, and `ParameterError` data structures;
- the `GeneratorData.from_dict()` component-registry and endpoint-collection workflow;
- local component `$ref` parsing and request/response reference restrictions;
- operation-level versus path-level parameter precedence;
- path-parameter sorting and generated operation IDs;
- ordering exact response codes, response-code ranges, and the default response;
- request-body reference traversal and circular-reference detection.

The workflow is retained while its output is rewritten for Spitzeisen's endpoint and schema-type IR.
The upstream project is Copyright (c) 2020 Triax Technologies and distributed under the MIT License.
The complete license text is included at `licenses/openapi-python-client.txt`.

Upstream project: <https://github.com/openapi-generators/openapi-python-client>

Other third-party packages used as dependencies retain their own package metadata and licenses.
