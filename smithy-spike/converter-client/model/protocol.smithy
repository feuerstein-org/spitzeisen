$version: "2"

namespace vendor

use aws.protocols#restJson1

// OpenAPI describes HTTP bindings but not the Smithy protocol required by smithy-python.
apply VendorService @restJson1

// smithy-python 0.5.0 emits a client that dereferences auth configuration even when a
// service has no auth traits. A header API key lets this spike exercise the converted
// operations without also hitting smithy-http's query API-key path corruption bug.
// This is test scaffolding, not a claim about the vendor's real authentication scheme.
apply VendorService @httpApiKeyAuth(name: "Authorization", in: "header", scheme: "Bearer")
