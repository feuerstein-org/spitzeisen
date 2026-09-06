"""The code-generation toolchain pinned by the packaged Smithy integration."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

from spitzeisen.codegen.exceptions import CodegenError

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
ALLOY_VERSION = _PROPERTIES["alloy.version"]
ALLOY_CORE_COORDINATE = f"com.disneystreaming.alloy:alloy-core:{ALLOY_VERSION}"


@functools.cache
def alloy_model_path() -> Path:
    """Resolve the pinned Alloy model JAR through the same cache as the Smithy launcher."""
    coursier = shutil.which("coursier") or shutil.which("cs")
    if coursier is None:
        raise CodegenError(header="Alloy model dependency is unavailable", detail="Install Java and Coursier.")
    process = subprocess.run(  # noqa: S603 - fixed public dependency coordinate
        [coursier, "fetch", "--classpath", ALLOY_CORE_COORDINATE],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode == 0:
        for entry in process.stdout.strip().split(os.pathsep):
            path = Path(entry)
            if path.name == f"alloy-core-{ALLOY_VERSION}.jar" and path.is_file():
                return path
    raise CodegenError(
        header="Unable to resolve the pinned Alloy model",
        detail=process.stderr.strip() or f"Coursier did not return {ALLOY_CORE_COORDINATE}",
    )
