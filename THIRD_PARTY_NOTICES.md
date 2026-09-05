# Third-party notices

Spitzeisen does not vendor OpenAPI parser or schema source code.

The optional code-generation workflow launches
[smithy-translate](https://github.com/disneystreaming/smithy-translate) version 0.7.8 as an external
tool under its Apache-2.0 license. The artifact and its transitive dependencies are resolved by
Coursier and retain their own package metadata and licenses.

The workflow also launches the official
[Smithy CLI](https://github.com/smithy-lang/smithy) version 1.73.0 as an external tool under its
Apache-2.0 license. Its artifact and transitive dependencies are resolved by Coursier and retain
their own package metadata and licenses.

Native Smithy model generation uses the official `smithy-jsonschema` library at the same pinned
version through Spitzeisen's Smithy Build plugin. The library is provided under Smithy's Apache-2.0
license and is resolved by Coursier rather than vendored. The bundled plugin JAR contains only
Spitzeisen's compiled generator, Smithy trait definitions, and the notice below; it is not a fat
JAR of dependencies.

## smithy-python writer concepts and adaptation

The small Java `PythonWriter` adapts the symbol/import approach from
[smithy-python](https://github.com/smithy-lang/smithy-python), specifically `PythonWriter.java`
and `ImportDeclarations.java` in `codegen/core`, reviewed at commit
`19384f35afc97b365b5bf30b8a8889c6428c3b32`.

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
Licensed under the Apache License, Version 2.0. The full license is included at
`codegen/spitzeisen-smithy-codegen/src/main/resources/META-INF/licenses/smithy-python-LICENSE`
and in the bundled generator JAR. Spitzeisen's adaptation uses eager alias allocation, absolute
imports, reserved local names, and standalone Python literals. It omits upstream runtime,
Markdown/Pandoc, dependency, and module-generation machinery. No upstream Python runtime is vendored.

Python packages used as runtime, development, and optional code-generation dependencies retain
their own package metadata and licenses; their resolved versions are recorded in `uv.lock`.
