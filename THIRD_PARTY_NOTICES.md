# Third-party notices

Spitzeisen does not vendor OpenAPI parser or schema source code.

The optional code-generation workflow launches
[smithy-translate](https://github.com/disneystreaming/smithy-translate) version 0.7.8 as an external
tool under its Apache-2.0 license. The artifact and its transitive dependencies are resolved by
Coursier and retain their own package metadata and licenses.

The workflow also launches the official
[Smithy CLI](https://github.com/smithy-lang/smithy) version 1.72.0 as an external tool under its
Apache-2.0 license. Its artifact and transitive dependencies are resolved by Coursier and retain
their own package metadata and licenses.

Python packages used as runtime, development, and optional code-generation dependencies retain
their own package metadata and licenses; their resolved versions are recorded in `uv.lock`.
