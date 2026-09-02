"""The code-generation toolchain pinned by the packaged Smithy integration."""

from __future__ import annotations

from pathlib import Path

_TOOLCHAIN_PROPERTIES = Path(__file__).with_name("smithy") / "toolchain.properties"


def _read_properties() -> dict[str, str]:
    """Load the tiny cross-language toolchain manifest with useful errors."""
    properties: dict[str, str] = {}
    for raw_line in _TOOLCHAIN_PROPERTIES.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or not value:
            msg = f"invalid codegen toolchain property: {line!r}"
            raise RuntimeError(msg)
        properties[key] = value
    return properties


_PROPERTIES = _read_properties()
JAVA_VERSION = int(_PROPERTIES["java.version"])
SMITHY_VERSION = _PROPERTIES["smithy.version"]
