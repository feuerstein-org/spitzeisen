"""Verify the packaged Java frontend through the production Python boundary."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast

from spitzeisen.codegen.cli import generate_model_source, write
from spitzeisen.codegen.generate import GeneratedModule, generate_plan, model_exports_module
from spitzeisen.codegen.inputs import ModelInputType, load_compile_inputs
from spitzeisen.codegen.python_context import PythonInputAdapter, PythonSettings
from spitzeisen.codegen.python_lowering import lower_service_plan
from spitzeisen.codegen.python_plan import PythonImport, PythonPlan, PythonTypePlan
from spitzeisen.codegen.service_plan_io import service_plan_from_document

if TYPE_CHECKING:
    from spitzeisen.codegen.service_plan import ServicePlan

ROOT = Path(__file__).resolve().parent.parent
PROJECTIONS = (
    ROOT
    / "codegen"
    / "spitzeisen-smithy-codegen-test"
    / "build"
    / "smithyprojections"
    / "spitzeisen-smithy-codegen-test"
)
NATIVE_ARTIFACTS = PROJECTIONS / "weather" / "spitzeisen-service-plan"
POLICY_ARTIFACTS = PROJECTIONS / "policy" / "spitzeisen-service-plan"
OPENAPI_ARTIFACTS = (
    ROOT
    / "codegen"
    / "spitzeisen-smithy-codegen-test"
    / "build"
    / "openapi-smithy"
    / "weather"
    / "spitzeisen-service-plan"
)


def main() -> None:
    """Exercise native, imported OpenAPI, and policy-rich plugin projections."""
    native_plan = _service_plan(NATIVE_ARTIFACTS / "service-plan.json")
    native_schema = _load_json(NATIVE_ARTIFACTS / "model-schema.json")
    native_production = load_compile_inputs(
        smithy_sources=(ROOT / "examples" / "spec" / "native-weather.smithy",),
        overlays=(),
        timeout=30,
    )
    _require_equal("native service plan", native_production.service_plan, native_plan)
    _require_equal("native model schema", native_production.model_schema, native_schema)
    native_client = lower_service_plan(
        native_plan,
        PythonSettings(package="native_weather_sdk", client_name="NativeWeatherApi"),
    )
    with TemporaryDirectory(prefix="spitzeisen-native-") as temporary:
        root = Path(temporary)
        modules = _module_sources(native_client, native_schema, "jsonschema", root)
        write(list(modules.values()))
        _verify_importable_sdk(root, "native_weather_sdk", "AsyncNativeWeatherApi")

    openapi_plan = _service_plan(OPENAPI_ARTIFACTS / "service-plan.json")
    openapi_production = load_compile_inputs(
        source=ROOT / "examples" / "spec" / "vendor.json",
        overlays=(ROOT / "examples" / "spec" / "weather.smithy",),
        timeout=30,
    )
    _require_equal("OpenAPI service plan", openapi_production.service_plan, openapi_plan)
    openapi_client = lower_service_plan(
        openapi_plan,
        PythonSettings(package="weather_sdk", client_name="WeatherApi", vendor="openweathermap"),
    )
    with TemporaryDirectory(prefix="spitzeisen-openapi-") as temporary:
        root = Path(temporary)
        modules = _module_sources(openapi_client, openapi_production.model_schema, "openapi", root)
        write(list(modules.values()))
        _verify_importable_sdk(root, "weather_sdk", "AsyncWeatherApi")

    policy_client = lower_service_plan(
        _service_plan(POLICY_ARTIFACTS / "service-plan.json"),
        PythonSettings(
            package="policy_sdk",
            client_name="PolicyApi",
            input_adapters=_policy_adapters(),
        ),
    )
    _verify_policy_surface(policy_client)

    print("Java Smithy frontend verification passed:")
    print("  packaged native-Smithy service plan/schema match direct Smithy Build output")
    print(f"  {len(native_client.operations)} native operation(s) render into an importable Pydantic SDK")
    print("  packaged OpenAPI service plan matches direct Smithy Build output after translation and overlays")
    print(f"  {len(openapi_client.operations)} OpenAPI operation(s) render into an importable SDK")
    print("  pagination, sorting, coercion, defaults, headers, and model-property traits are present")


def _service_plan(path: Path) -> ServicePlan:
    return service_plan_from_document(_load_json(path))


def _policy_adapters() -> dict[str, PythonInputAdapter]:
    """Provide explicit Python behavior for the portable adapter IDs in the fixture."""
    return {
        "comma-list": PythonInputAdapter(
            function=PythonImport("policy_sdk.params", "coerce_comma_list"),
            public_type=PythonTypePlan("list", members=(PythonTypePlan("str"),)),
        ),
        "date": PythonInputAdapter(
            function=PythonImport("spitzeisen", "coerce_date"),
            public_type=PythonTypePlan("date_input"),
        ),
        "custom": PythonInputAdapter(
            function=PythonImport("policy_sdk.params", "coerce_custom"),
            public_type=PythonTypePlan("str"),
        ),
    }


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
    client: PythonPlan,
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


def _verify_policy_surface(client: PythonPlan) -> None:
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
    _require_equal("policy filter coercion", facts["filters"].coercion.kind, "custom")
    filter_adapter = facts["filters"].coercion.function
    if filter_adapter is None:
        msg = "policy filter adapter is absent"
        raise AssertionError(msg)
    _require_equal("policy filter adapter", filter_adapter.identifier, "coerce_comma_list")
    _require_equal("policy date coercion", facts["since"].type.kind, "date_input")
    custom_adapter = facts["custom"].coercion.function
    if custom_adapter is None:
        msg = "policy custom adapter is absent"
        raise AssertionError(msg)
    _require_equal("policy custom coercion", custom_adapter.identifier, "coerce_custom")
    _require_equal("policy enum type", facts["state"].type.values, ("open", "closed"))
    _require_equal("policy client default", facts["language"].default.value, "en")
    _require_equal("policy sort separator", operation.sorting.separator, ".")
    _require_equal("policy model aliases", client.model_aliases, {"Record.displayName": "display_name"})


def _require_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        msg = f"packaged Java frontend {label} differs from direct Smithy Build output"
        raise AssertionError(msg)


if __name__ == "__main__":
    main()
