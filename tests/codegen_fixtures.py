"""Small artifact fixtures for offline tests of Python's build orchestration."""

from pathlib import Path
from typing import Any

from spitzeisen.codegen.artifacts import ArtifactManifest
from spitzeisen.codegen.generate import GeneratedModule
from spitzeisen.codegen.java_frontend import FrontendResult


def frontend_result(schema: dict[str, Any] | None = None) -> FrontendResult:
    """Represent already-generated Java output without recreating any Smithy semantics."""
    modules = [
        GeneratedModule(Path("__init__.py"), '"""Public extension."""\n', create_once=True),
        GeneratedModule(Path("_async/_generated/client.py"), "VALUE = 1\n"),
    ]
    return FrontendResult(
        manifest=ArtifactManifest(
            files={module.path.as_posix(): module.create_once for module in modules},
            models=[],
            model_names={},
            model_aliases={},
            dependencies=[],
            warnings=[],
        ),
        modules=modules,
        model_schema=schema or {},
    )
