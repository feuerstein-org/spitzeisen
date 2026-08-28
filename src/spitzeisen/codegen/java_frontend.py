"""Run Spitzeisen's packaged Smithy Build plugin and load its renderer contract."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from spitzeisen.codegen.assembly import SMITHY_CLI_COORDINATE, SMITHY_CLI_VERSION
from spitzeisen.codegen.exceptions import CodegenError
from spitzeisen.codegen.plan_io import client_plan_from_document

if TYPE_CHECKING:
    from spitzeisen.codegen.policy import ClientPlan, TargetSettings

SMITHY_JSONSCHEMA_COORDINATE = f"software.amazon.smithy:smithy-jsonschema:{SMITHY_CLI_VERSION}"
PLUGIN_NAME = "spitzeisen-python-client-codegen"
PLUGIN_JAR = Path(__file__).with_name("smithy") / "spitzeisen-codegen.jar"


@dataclass(frozen=True, slots=True)
class FrontendResult:
    """Outputs emitted by the Java Smithy semantic frontend."""

    client: ClientPlan
    model_schema: dict[str, Any]


def compile_smithy_frontend(
    model: dict[str, Any],
    *,
    target: TargetSettings,
    working_directory: Path | None = None,
) -> FrontendResult:
    """Compile an assembled Smithy model with the packaged production plugin."""
    command = _frontend_command()
    settings: dict[str, object] = {
        "package": target.package,
        "clientName": target.client_name,
    }
    if service := _service_id(model, target.service):
        settings["service"] = service
    if target.vendor is not None:
        settings["vendor"] = target.vendor
    config = {
        "version": "1.0",
        "projections": {
            "spitzeisen": {
                "plugins": {PLUGIN_NAME: settings},
            },
        },
    }

    if working_directory is not None:
        working_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".spitzeisen-frontend-", dir=working_directory) as temporary:
        root = Path(temporary)
        source = root / "model.smithy.json"
        build_config = root / "smithy-build.json"
        output = root / "output"
        source.write_text(json.dumps(model, indent=2))
        build_config.write_text(json.dumps(config, indent=2))
        process = subprocess.run(  # noqa: S603
            [
                *command,
                "build",
                "--quiet",
                "--config",
                str(build_config),
                "--output",
                str(output),
                str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        artifact_root = output / "spitzeisen" / PLUGIN_NAME
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).strip() or (
                f"Smithy Build exited with status {process.returncode}"
            )
            raise CodegenError(header="Smithy client-plan compilation failed", detail=detail)
        plan_document = _load_artifact(artifact_root / "client-plan.json", "client plan")
        model_schema = _load_artifact(artifact_root / "model-schema.json", "model schema")
    try:
        client = client_plan_from_document(plan_document)
    except (TypeError, ValueError) as err:
        raise CodegenError(header="Smithy frontend emitted an invalid client plan", detail=str(err)) from err
    return FrontendResult(client=client, model_schema=model_schema)


def _frontend_command() -> list[str]:
    """Resolve Coursier and the packaged, pinned plugin dependencies."""
    if not PLUGIN_JAR.is_file():  # pragma: no cover - packaging integrity guard
        raise CodegenError(
            header="Smithy codegen plugin is unavailable",
            detail=f"the installed package is missing {PLUGIN_JAR}",
        )
    coursier = shutil.which("coursier") or shutil.which("cs")
    if coursier is None:
        raise CodegenError(
            header="Smithy codegen frontend is unavailable",
            detail="Install Java and Coursier; Spitzeisen uses them to launch its pinned Smithy Build plugin.",
        )
    return [
        coursier,
        "launch",
        SMITHY_CLI_COORDINATE,
        SMITHY_JSONSCHEMA_COORDINATE,
        "--extra-jars",
        str(PLUGIN_JAR),
        "--",
    ]


def _service_id(model: dict[str, Any], requested: str | None) -> str | None:
    """Resolve the former local-name convenience before passing a ShapeId to Java."""
    raw_shapes = model.get("shapes")
    shapes = cast("dict[str, object]", raw_shapes) if isinstance(raw_shapes, dict) else {}
    services = [
        shape_id
        for shape_id, value in shapes.items()
        if isinstance(value, dict) and cast("dict[str, object]", value).get("type") == "service"
    ]
    if requested is None:
        return services[0] if len(services) == 1 else None
    normalized = requested.casefold()
    matches = [
        shape_id
        for shape_id in services
        if shape_id.casefold() == normalized or shape_id.rsplit("#", maxsplit=1)[-1].casefold() == normalized
    ]
    if len(matches) != 1:
        msg = f"service {requested!r} is absent or ambiguous; available services: {services}"
        raise CodegenError(detail=msg)
    return matches[0]


def _load_artifact(path: Path, label: str) -> dict[str, Any]:
    """Load one required JSON object emitted by the plugin."""
    try:
        value: object = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        raise CodegenError(header=f"Smithy frontend emitted an invalid {label}", detail=str(err)) from err
    if not isinstance(value, dict):
        raise CodegenError(
            header=f"Smithy frontend emitted an invalid {label}",
            detail=f"{path.name} must contain a JSON object",
        )
    return cast("dict[str, Any]", value)
