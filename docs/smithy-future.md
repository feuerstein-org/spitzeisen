# Deferred Smithy generation

Status: deferred on 2026-09-08. Spitzeisen's supported product is the shared Python runtime for
handwritten SDKs. Generation is a possible future consumer of that runtime; it is not needed to
build, test, install, or use it. Java, Gradle, Coursier, the generator CLI, its optional Python
packages, and generated SDK fixtures have been removed from this branch.

The initial implementation already separated authentication, request/retry handling, connection
ownership, pagination, rate limiting, validation, and parameter conversion from vendor endpoints.
That is the useful boundary to develop first. SDK authors own paths, Python signatures, response
models, vendor defaults, and operation-specific policy. They call the runtime's public helpers.
Neither handwritten SDKs nor a future generator should need to copy the HTTP execution pipeline.
The mechanical async-to-sync build of the runtime remains; it does not depend on a service model.

## Why defer

The experiment accumulated several independent support commitments: OpenAPI ingestion and semantic
translation, Smithy model assembly, HTTP protocol coverage, Python symbol generation, Pydantic
schema generation and validation policy, generated-file ownership, packaging a Java plugin, and
coordinating their toolchains. Maintaining all of them is premature before the shared runtime has
proved useful in handwritten SDKs such as massive-api.

This decision does not discard the runtime improvements added during the experiment: public
requests with custom decoders, replayable request bodies, explicit retry policy for non-idempotent
methods, useful HTTP error metadata, and per-client validation context are useful independently.

## Recovering the experiment

The preserved `openapi-smithy-pipeline` branch points to the last generator implementation at the
time of this decision. Use immutable commits when reviewing the design later, because branch names
can move:

| Commit | What it preserves |
| --- | --- |
| `a2f2674a63be581b202085461a4ac17cd571f845` | Initial neutral Python runtime: strategy protocols, cached batch validation, shared client lifecycle, and unasync build. |
| `add3716df2082c05b5a5ad67116fd087ca22b187` | Early handwritten-client documentation alongside the first optional generator. |
| `bf23dab8bac6bbdc2ecbbc19360b8c9dd0c780b4` | Last pre-Smithy generator branch (`codegen`). |
| `d110de09c01625642bda350124bcec5aa6645b63` | Official Smithy Python feasibility spike, runnable fixtures, and recorded results. |
| `7b313beae45b7ba3ab6160ddbab784ffa772f7c9` | Smithy client usability comparison. |
| `ab5aaf79bdd636a5a2ec32b36949a3b59d408c38` | SDK policies moved into Smithy traits. |
| `48489e23b15cc29b26483263e5d7808d21ea846c` | Java Smithy frontend migration. |
| `92fd891c7a0f0573e21b51f1dd178181f8c8071a` | Intermediate language-neutral plan, subsequently removed. |
| `5d1fe6269fef6fc6ab7bc6a3301a23c0f7a09c12` | Java owns Python generation directly. |
| `0f2d1a6fb861d4018532fa4ed3f30b0af9666519` | Last implementation: Smithy codegen lifecycle and bounded Alloy protocol support. |

Inspect the recorded architecture and spike without changing the active checkout:

```bash
git show 0f2d1a6:docs/codegen-architecture.md
git show 0f2d1a6:docs/alloy-client.md
git show d110de0:smithy-spike/RESULTS.md
git show 7b313be:smithy-spike/CLIENT_USABILITY.md
```

The complete Java plugin, pinned dependencies, Python launcher, generation tests, examples, and
third-party notices are in the final commit. Historical validation results describe those revisions,
not the current state of upstream projects. Recheck upstream status before making a new decision.

## Architecture worth keeping if generation returns

Use Smithy as the semantic source of truth and the official assembler for model preparation. A
language target should consume the assembled model directly. The last design used a Smithy Build
plugin with `CodegenDirector`/`DirectedCodegen`, `SymbolProvider`, `SymbolWriter`, `WriterDelegator`,
`FileManifest`, and Smithy's operation, binding, nullability, and service indexes. This avoided
maintaining a second serialized operation graph or an independent semantic interpreter in Python.

The Java target produced Python client source directly. The Python wrapper launched pinned tools,
ran the Pydantic model backend over a response-only JSON Schema, formatted and checked output, and
maintained generated-file ownership. The small temporary manifest described output files, public
model exports, name mappings, and dependency requirements. It was a private build interface.

Keep portable service policies separate from Python presentation. Portable traits covered result
envelopes, pagination, 404 behavior, rate-limit costs, sorting, client defaults, and exclusions.
Python traits supplied names and module layout. Ordinary Smithy documentation, HTTP bindings,
defaults, enums, timestamps, and constraints remained authoritative. A future target should express
these policies using the same public runtime API handwritten SDKs use, without moving vendor
semantics into Spitzeisen.

Generation had replaceable implementation files and create-once public model, operation, and client
subclasses. All source was validated before replacement. Replacement was atomic per file, not a
transaction for the entire SDK. A future implementation must preserve user customization and test
failure behavior explicitly.

## Recorded limitations to revisit

At the final experimental revision, the Python target implemented a bounded GET/JSON subset of
`alloy#simpleRestJson`. It handled path labels, repeated query values, query maps, scalar headers,
and selected pagination and result policies. It explicitly rejected unsupported bindings and shape
kinds; it did not claim complete Alloy conformance. Non-GET bodies and custom response formats were
available through the runtime, but not through the generator.

The pinned OpenAPI importer was `smithy-translate` 0.7.8. Ordinary OpenAPI 3.1 scalar schemas produced
placeholder structures in the recorded spike. The importer also dropped some OpenAPI nullability
and default semantics, requiring validation and explicit overlays. Merely changing a document's
version field is not a general conversion strategy. Prefer native Smithy when the service model is
under our control; otherwise validate the converter's output and reject unsupported semantics.
The [archived reproduction](archive/smithy-translate-openapi-3.1.md),
[proposed patch](archive/smithy-translate-openapi-3.1.patch), and
[PR draft](archive/smithy-translate-openapi-3.1-pr.md) retain the prior upstream work; they are not an
active fork or a claim that a fix has been released.

The official Python-generator spike recorded different public API choices: input dataclasses,
async-only clients, and missing paginator and validation behavior relative to this project's SDK
contract. Those were observations from August 2026, not assumptions to carry into a future review.
Adopting upstream output is a separate decision from adopting Smithy's modeling infrastructure.

Before resuming, demonstrate a maintenance benefit with representative operations from multiple
SDKs. Agree on the supported protocol and customization contract, measure what can be reused from
upstream, and rerun fixtures for pagination, aliases, optionality, errors, retries, and both Python
surfaces. Ship generation as separate tooling so consumers of the core keep a Python-only install.
Restore the archived implementation's dependency notices and licenses if its code is reused.
