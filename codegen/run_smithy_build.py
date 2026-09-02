"""Run a direct Smithy Build projection with Spitzeisen's centrally pinned toolchain."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.java_frontend import smithy_build_command


def main() -> None:
    """Launch one projection without repeating Maven coordinates in task configuration."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-jar", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("source", type=Path)
    arguments = parser.parse_args()
    try:
        command = smithy_build_command(arguments.plugin_jar)
    except CodegenError as err:
        parser.error(f"{err.header}: {err.detail}")
    result = subprocess.run(  # noqa: S603
        [
            *command,
            "build",
            "--quiet",
            "--config",
            str(arguments.config),
            "--output",
            str(arguments.output),
            str(arguments.source),
        ],
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
