"""Verify the packaged Java frontend through the production Python boundary."""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Container
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from spitzeisen.codegen.cli import generate_model_source, write
from spitzeisen.codegen.generate import GeneratedModule, generate_plan, model_exports_module
from spitzeisen.codegen.inputs import ModelInputType, load_compile_inputs
from spitzeisen.codegen.plan import ClientPlan, TargetSettings
from spitzeisen.codegen.plan_io import client_plan_from_document

ROOT = Path(__file__).resolve().parent.parent
PROJECTIONS = (
    ROOT
    / "codegen"
    / "spitzeisen-smithy-codegen-test"
    / "build"
    / "smithyprojections"
    / "spitzeisen-smithy-codegen-test"
)
NATIVE_ARTIFACTS = PROJECTIONS / "weather" / "spitzeisen-python-client-codegen"
POLICY_ARTIFACTS = PROJECTIONS / "policy" / "spitzeisen-python-client-codegen"
OPENAPI_ARTIFACTS = (
    ROOT
    / "codegen"
    / "spitzeisen-smithy-codegen-test"
    / "build"
    / "openapi-smithy"
    / "weather"
    / "spitzeisen-python-client-codegen"
)


def main() -> None:
    """Exercise native, imported OpenAPI, and policy-rich plugin projections."""
    native_client = _client(NATIVE_ARTIFACTS / "client-plan.json")
    native_schema = _load_json(NATIVE_ARTIFACTS / "model-schema.json")
    native_production = load_compile_inputs(
        smithy_sources=(ROOT / "examples" / "spec" / "native-weather.smithy",),
        overlays=(),
        target=TargetSettings(package="native_weather_sdk", client_name="NativeWeatherApi"),
        timeout=30,
    )
    _require_equal("native client plan", native_production.client, native_client)
    _require_equal("native model schema", native_production.model_schema, native_schema)
    with TemporaryDirectory(prefix="spitzeisen-native-") as temporary:
        root = Path(temporary)
        modules = _module_sources(native_client, native_schema, "jsonschema", root)
        write(list(modules.values()))
        _verify_importable_sdk(root, "native_weather_sdk", "AsyncNativeWeatherApi")

    openapi_client = _client(OPENAPI_ARTIFACTS / "client-plan.json")
    openapi_production = load_compile_inputs(
        source=ROOT / "examples" / "spec" / "vendor.json",
        overlays=(ROOT / "examples" / "spec" / "weather.smithy",),
        target=TargetSettings(package="weather_sdk", client_name="WeatherApi", vendor="openweathermap"),
        timeout=30,
    )
    _require_equal("OpenAPI client plan", openapi_production.client, openapi_client)
    with TemporaryDirectory(prefix="spitzeisen-openapi-") as temporary:
        root = Path(temporary)
        modules = _module_sources(openapi_client, openapi_production.model_schema, "openapi", root)
        write(list(modules.values()))
        _verify_importable_sdk(root, "weather_sdk", "AsyncWeatherApi")

    policy_client = _client(POLICY_ARTIFACTS / "client-plan.json")
    _verify_policy_surface(policy_client)

    print("Java Smithy frontend verification passed:")
    print("  packaged native-Smithy plan/schema match direct Smithy Build output")
    print(f"  {len(native_client.operations)} native operation(s) render into an importable Pydantic SDK")
    print("  packaged OpenAPI plan matches direct Smithy Build output after translation and overlays")
    print(f"  {len(openapi_client.operations)} OpenAPI operation(s) render into an importable SDK")
    print("  pagination, sorting, coercion, defaults, headers, and model-property traits are present")


def _client(path: Path) -> ClientPlan:
    return client_plan_from_document(_load_json(path))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value: object = json.loads(path.read_text())
    except OSError as err:
        msg = f"missing frontend artifact {path}; run `mise run smithy-java-spike`"
        raise RuntimeError(msg) from err
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
            msg = f"generated package does not export {client_name}"
            raise AssertionError(msg)
        if package_name == "native_weather_sdk":
            weather = package.Weather.model_validate(
                {"temperature": 20, "status": "clear", "futureField": True},
            )
            _require_equal("native temperature", weather.temperature, 20)
            if hasattr(weather, "futureField"):
                msg = "frontend produced an invalid Pydantic response model"
                raise AssertionError(msg)
    finally:
        sys.path.remove(str(output_root))
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(f"{package_name}."):
                del sys.modules[name]


def _verify_policy_surface(client: ClientPlan) -> None:
    operation = client.operations[0]
    facts = {param.name: param for param in operation.params}
    _require_equal("policy pagination", operation.pagination, "page_number")
    _require_equal("policy page start", operation.page_start, 0)
    _require_equal("policy page step", operation.page_step, 2)
    if operation.page_size is None:
        msg = "policy page size is absent"
        raise AssertionError(msg)
    _require_equal("policy page-size maximum", operation.page_size.maximum, 500)
    if operation.sorting is None:
        msg = "policy sorting is absent"
        raise AssertionError(msg)
    _require_equal("policy sorting style", operation.sorting.style, "suffix")
    _require_equal("policy filter coercion", facts["filters"].coercion, '",".join(filters) if filters else None')
    _require_contains("policy date coercion", "coerce_date(", facts["since"].coercion)
    _require_contains("policy function import", "coerce_custom", operation.coerce_function_imports)
    _require_equal("policy model imports", operation.model_imports, ("StateAlias",))
    _require_equal("policy model aliases", client.model_aliases, {"Record.displayName": "display_name"})
    _require_equal("policy model types", client.model_type_overrides, {"Record.displayName": "str"})


def _require_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        msg = f"packaged Java frontend {label} differs from direct Smithy Build output"
        raise AssertionError(msg)


def _require_contains(label: str, needle: str, values: Container[str]) -> None:
    if needle not in values:
        msg = f"packaged Java frontend {label} is absent"
        raise AssertionError(msg)


if __name__ == "__main__":
    main()
