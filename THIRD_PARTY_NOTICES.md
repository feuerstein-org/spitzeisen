# Third-party notices

## openapi-python-client

Portions of the OpenAPI lowering behavior in `src/spitzeisen/codegen/lower.py` are adapted from
openapi-python-client 0.29.0, specifically:

- operation-level versus path-level parameter precedence;
- ordering exact response codes, response-code ranges, and the default response.

The implementation was rewritten for Spitzeisen's immutable, generator-neutral IR. The upstream
project is Copyright (c) 2020 Triax Technologies and distributed under the MIT License. The complete
license text is included at `licenses/openapi-python-client.txt`.

Upstream project: <https://github.com/openapi-generators/openapi-python-client>

Other third-party packages used as dependencies retain their own package metadata and licenses; no
source from them is vendored into this repository.
