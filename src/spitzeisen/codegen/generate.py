"""Finalize Java-emitted sources and expose the Pydantic backend's public model classes."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spitzeisen.codegen.artifacts import ModelArtifact


@dataclass(frozen=True, slots=True)
class GeneratedModule:
    """One generated file, or a create-once extension owned by the SDK author."""

    path: Path
    source: str
    create_once: bool = False


def formatted_module(path: Path, source: str, *, package: str, create_once: bool = False) -> GeneratedModule:
    """Format and syntax-check a module before any destination files are replaced."""
    formatted = format_python(source, str(path), package=package)
    try:
        ast.parse(formatted, filename=str(path))
    except SyntaxError as err:
        msg = f"generator produced invalid Python for {path}: {err.msg} (line {err.lineno})"
        raise ValueError(msg) from err
    return GeneratedModule(path, formatted, create_once=create_once)


def format_python(source: str, filename: str = "generated.py", *, package: str | None = None) -> str:
    """Apply the same import sorting and formatting to both generation backends."""
    import_config = (
        ["--config", f"lint.isort.known-first-party={json.dumps([package, 'spitzeisen'])}"]
        if package is not None
        else []
    )
    for argv in (
        [
            python_tool("ruff"),
            "check",
            "--select",
            "I,F401",
            "--fix",
            "--quiet",
            "--stdin-filename",
            filename,
            *import_config,
            "-",
        ],
        [python_tool("ruff"), "format", "--quiet", "--stdin-filename", filename, "-"],
    ):
        result = subprocess.run(argv, input=source, capture_output=True, text=True, check=False)  # noqa: S603
        if result.returncode != 0:
            msg = f"could not format {filename}: {result.stderr or result.stdout}"
            raise ValueError(msg)
        source = result.stdout
    return source


def python_tool(name: str) -> str:
    """Resolve a console script, including beside the active Python executable."""
    if executable := shutil.which(name):
        return executable
    adjacent = Path(sys.executable).with_name(name)
    return str(adjacent) if adjacent.is_file() else name


def generated_model_names(source: str) -> list[str]:
    """Return public classes actually emitted by datamodel-code-generator."""
    tree = ast.parse(source)
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")]


def model_exports_module(
    package: str,
    models: list[ModelArtifact],
    package_root: Path,
    model_source: str,
) -> GeneratedModule:
    """Publish model classes, preferring create-once subclasses for operation results."""
    names = sorted(generated_model_names(model_source))
    public = {model.name: model.module for model in models if model.generated}
    source = '"""Generated public model exports. Do not edit."""\n'
    source += "".join(f"from {public.get(name, f'{package}.models._generated')} import {name}\n" for name in names)
    source += f"\n__all__ = {names!r}\n"
    return formatted_module(package_root / "models" / "_exports.py", source, package=package)
