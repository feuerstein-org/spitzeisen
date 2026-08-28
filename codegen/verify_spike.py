"""Verify that the Java Smithy frontend preserves Spitzeisen's Python output contract."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from spitzeisen.codegen.assembly import assemble_smithy
from spitzeisen.codegen.cli import generate_model_source, write
from spitzeisen.codegen.generate import GeneratedModule, generate_plan, model_exports_module
from spitzeisen.codegen.inputs import ModelInputType, load_document, parse_smithy
from spitzeisen.codegen.openapi import import_openapi
from spitzeisen.codegen.plan_io import client_plan_document, client_plan_from_document
from spitzeisen.codegen.policy import ClientPlan, TargetSettings, compile_model
from spitzeisen.codegen.traits import model_customizations

ROOT = Path(__file__).resolve().parent.parent
NATIVE_ARTIFACT_ROOT = (
    ROOT
    / "codegen"
    / "spitzeisen-smithy-codegen-test"
    / "build"
    / "smithyprojections"
    / "spitzeisen-smithy-codegen-test"
    / "weather"
    / "spitzeisen-python-client-codegen"
)
OPENAPI_ARTIFACT_ROOT = (
    ROOT
    / "codegen"
    / "spitzeisen-smithy-codegen-test"
    / "build"
    / "openapi-smithy"
    / "weather"
    / "spitzeisen-python-client-codegen"
)
POLICY_ARTIFACT_ROOT = (
    ROOT
    / "codegen"
    / "spitzeisen-smithy-codegen-test"
    / "build"
    / "smithyprojections"
    / "spitzeisen-smithy-codegen-test"
    / "policy"
    / "spitzeisen-python-client-codegen"
)
EXPECTED_TEMPERATURE = 20


def main() -> None:
    """Compare semantic output, model schema, and rendered Python modules."""
    plan_path = NATIVE_ARTIFACT_ROOT / "client-plan.json"
    schema_path = NATIVE_ARTIFACT_ROOT / "model-schema.json"
    if not plan_path.is_file() or not schema_path.is_file():
        msg = "run `codegen/gradlew -p codegen :spitzeisen-smithy-codegen-test:smithyBuild` first"
        raise SystemExit(msg)

    java_document = _load_json(plan_path)
    java_schema = _load_json(schema_path)
    java_client = client_plan_from_document(java_document)
    native_source = ROOT / "examples" / "spec" / "native-weather.smithy"
    native_model = assemble_smithy(
        {"smithy": "2.0", "shapes": {}},
        (native_source,),
    )
    native_target = TargetSettings(package="native_weather_sdk", client_name="NativeWeatherApi")
    current_client = compile_model(
        native_target,
        parse_smithy(native_model),
        model_customizations(native_model),
    )
    _require_equal("client plan", java_document, client_plan_document(current_client))
    with (
        TemporaryDirectory(prefix="spitzeisen-java-plan-") as java_dir,
        TemporaryDirectory(prefix="spitzeisen-python-plan-") as python_dir,
    ):
        java_root = Path(java_dir)
        java_modules = _module_sources(java_client, java_schema, "jsonschema", java_root)
        python_modules = _module_sources(current_client, java_schema, "jsonschema", Path(python_dir))
        write(list(java_modules.values()))
        _verify_importable_sdk(java_root, "native_weather_sdk", "AsyncNativeWeatherApi")
    _require_equal("rendered Python modules", _comparable_modules(java_modules), _comparable_modules(python_modules))

    openapi_plan = _load_json(OPENAPI_ARTIFACT_ROOT / "client-plan.json")
    openapi_java_client = client_plan_from_document(openapi_plan)
    openapi_source = ROOT / "examples" / "spec" / "vendor.json"
    openapi_overlay = ROOT / "examples" / "spec" / "weather.smithy"
    raw_openapi = load_document(source=openapi_source, timeout=30)
    imported = import_openapi(raw_openapi)
    openapi_model = assemble_smithy(imported.model, (openapi_overlay,))
    openapi_target = TargetSettings(
        package="weather_sdk",
        client_name="WeatherApi",
        vendor="openweathermap",
    )
    openapi_current = compile_model(
        openapi_target,
        parse_smithy(openapi_model),
        model_customizations(openapi_model),
    )
    _require_equal("OpenAPI-derived client plan", openapi_plan, client_plan_document(openapi_current))
    with (
        TemporaryDirectory(prefix="spitzeisen-java-openapi-") as java_dir,
        TemporaryDirectory(prefix="spitzeisen-python-openapi-") as python_dir,
    ):
        java_root = Path(java_dir)
        openapi_java_modules = _module_sources(
            openapi_java_client,
            raw_openapi,
            "openapi",
            java_root,
        )
        openapi_python_modules = _module_sources(
            openapi_current,
            raw_openapi,
            "openapi",
            Path(python_dir),
        )
        write(list(openapi_java_modules.values()))
        _verify_importable_sdk(java_root, "weather_sdk", "AsyncWeatherApi")
    _require_equal(
        "OpenAPI-derived Python modules",
        _comparable_modules(openapi_java_modules),
        _comparable_modules(openapi_python_modules),
    )

    policy_model = assemble_smithy(
        {"smithy": "2.0", "shapes": {}},
        (ROOT / "codegen" / "spitzeisen-smithy-codegen" / "src" / "test" / "resources" / "policy-features.smithy",),
    )
    policy_client = compile_model(
        TargetSettings(package="policy_sdk", client_name="PolicyApi"),
        parse_smithy(policy_model),
        model_customizations(policy_model),
    )
    _require_equal(
        "full trait-policy client plan",
        _load_json(POLICY_ARTIFACT_ROOT / "client-plan.json"),
        client_plan_document(policy_client),
    )

    print("Java Smithy frontend verification passed:")
    print("  native-Smithy client plan matches the legacy compiler")
    print(f"  {len(java_modules)} native-Smithy Python modules render byte-for-byte identically")
    print("  OpenAPI-derived client plan matches after Smithy translation and overlays")
    print(f"  {len(openapi_java_modules)} OpenAPI Python modules render byte-for-byte identically")
    print("  pagination, sorting, coercion, defaults, and model-property traits match the legacy compiler")


def _load_json(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text())
    if not isinstance(value, dict):
        msg = f"{path} must contain a JSON object"
        raise TypeError(msg)
    return cast("dict[str, Any]", value)


def _module_sources(
    client: ClientPlan,
    schema: dict[str, Any],
    input_type: ModelInputType,
    output_root: Path,
) -> dict[str, GeneratedModule]:
    package_root = output_root / client.package
    modules = generate_plan(client, package_root)
    model_source = generate_model_source(
        schema=schema,
        input_type=input_type,
        working_directory=output_root,
        client=client,
        package_root=package_root,
        base_class="spitzeisen.SpitzeisenModel",
        encoding="utf-8",
    )
    modules.append(GeneratedModule(package_root / "models" / "_generated.py", model_source))
    modules.append(model_exports_module(client, package_root, model_source))
    return {str(module.path.relative_to(package_root)): module for module in modules}


def _verify_importable_sdk(output_root: Path, package_name: str, client_name: str) -> None:
    sys.path.insert(0, str(output_root))
    try:
        package = importlib.import_module(package_name)
        if not hasattr(package, client_name):
            msg = f"Java frontend package does not export {client_name}"
            raise AssertionError(msg)
        if package_name == "native_weather_sdk":
            weather = package.Weather.model_validate(
                {"temperature": EXPECTED_TEMPERATURE, "status": "clear", "futureField": True},
            )
            if weather.temperature != EXPECTED_TEMPERATURE or hasattr(weather, "futureField"):
                msg = "Java frontend produced an invalid Pydantic response model"
                raise AssertionError(msg)
    finally:
        sys.path.remove(str(output_root))
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(f"{package_name}."):
                del sys.modules[name]


def _comparable_modules(modules: dict[str, GeneratedModule]) -> dict[str, tuple[str, bool]]:
    return {name: (module.source, module.create_once) for name, module in modules.items()}


def _require_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        msg = f"Java Smithy frontend {label} differs from the current Python frontend"
        raise AssertionError(msg)


if __name__ == "__main__":
    main()
