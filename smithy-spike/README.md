# Smithy Python spike

This directory is an isolated experiment. It does not change Spitzeisen's runtime or generator.
See [RESULTS.md](RESULTS.md) for the decision and detailed findings.
See [CLIENT_USABILITY.md](CLIENT_USABILITY.md) and the runnable
[`demo_client_usability.py`](demo_client_usability.py) for the consumer-facing comparison.

It contains two paths through the current Smithy Python generator:

1. `model/weather.smithy` is a handwritten representative service covering query authentication,
   GET and POST operations, modeled errors, pagination, constraints, and a custom rate-limit cost.
2. `converter-output-*` are outputs from `smithy-translate` 0.7.8 for the repository's OpenAPI 3.0
   and 3.1 fixtures. `converter-client` adds the protocol/auth overlay needed to feed the valid 3.0
   conversion to `smithy-python`.

`generated/handwritten` and `generated/converted` are checked-in snapshots. Smithy build trees and
virtual environments are ignored.

## Versions tested

- Smithy CLI 1.73.0
- `smithy-python` `b1e41e9d247fffb799dc12a174032fc3a78dbf2a` (2026-08-24), codegen 0.5.0
- `smithy-translate` 0.7.8
- Temurin JDK 25.0.4.1, Pandoc 3.10.2, Python 3.12

The Python generator is not published to Maven Central yet. Build the tested revision into the
local Maven repository first:

```bash
git clone https://github.com/smithy-lang/smithy-python.git /tmp/smithy-python
git -C /tmp/smithy-python checkout b1e41e9d247fffb799dc12a174032fc3a78dbf2a
cd /tmp/smithy-python/codegen
./gradlew publishToMavenLocal
```

With Java 25 or newer, Pandoc, and the Smithy CLI on `PATH`, regenerate the handwritten client with:

```bash
cd smithy-spike
smithy build
```

Regenerate the converted client after producing `converter-output-3.0/vendor.smithy` with:

```bash
cd smithy-spike/converter-client
smithy build
```

The behavioral tests live outside generated directories so regeneration cannot overwrite them.
Create each generated package environment with `uv sync --group test`, then run the matching file:

```bash
pytest -q /absolute/path/to/smithy-spike/tests/generated_client_checks.py
pytest -q /absolute/path/to/smithy-spike/tests/converted_client_checks.py
```
