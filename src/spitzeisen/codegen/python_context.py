"""Shared Python generation settings and resolved lowering context."""

from __future__ import annotations

import keyword
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from spitzeisen.codegen.python_integrations import PythonIntegration, select_integrations
from spitzeisen.codegen.python_protocols import PythonProtocol, PythonProtocolRegistry
from spitzeisen.codegen.python_symbols import PythonSymbolProvider

if TYPE_CHECKING:
    from spitzeisen.codegen.python_plan import PythonImport, PythonTypePlan
    from spitzeisen.codegen.service_plan import ServicePlan


def _empty_external_models() -> dict[str, PythonExternalModel]:
    return {}


def _empty_input_adapters() -> dict[str, PythonInputAdapter]:
    return {}


def _empty_dependencies() -> set[str]:
    return set()


@dataclass(frozen=True, slots=True)
class PythonExternalModel:
    """A response shape implemented by an existing importable Python model."""

    module: str
    symbol: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PythonInputAdapter:
    """A stable adapter ID's public type and ``function(value, *, param_name)`` import."""

    function: PythonImport
    public_type: PythonTypePlan
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PythonSettings:
    """Python artifact settings that never enter the target-neutral service plan."""

    package: str
    client_name: str
    vendor: str | None = None
    external_models: dict[str, PythonExternalModel] = field(default_factory=_empty_external_models)
    input_adapters: dict[str, PythonInputAdapter] = field(default_factory=_empty_input_adapters)
    protocol_preference: tuple[str, ...] = ()
    enabled_integrations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for segment in self.package.split("."):
            if not segment.isidentifier() or keyword.iskeyword(segment):
                msg = f"Python package segment {segment!r} is not a valid identifier"
                raise ValueError(msg)
        if not self.client_name.isidentifier() or keyword.iskeyword(self.client_name):
            msg = f"Python client name {self.client_name!r} is not a valid identifier"
            raise ValueError(msg)
        for shape_id, model in self.external_models.items():
            if "#" not in shape_id:
                msg = f"external model key {shape_id!r} must be a Smithy ShapeId"
                raise ValueError(msg)
            _validate_import(model.module, model.symbol, f"external model {shape_id!r}")
        for adapter_id, adapter in self.input_adapters.items():
            if not adapter_id:
                msg = "Python input adapter IDs cannot be empty"
                raise ValueError(msg)
            _validate_import(adapter.function.module, adapter.function.name, f"input adapter {adapter_id!r}")


@dataclass(slots=True)
class PythonGenerationContext:
    """Resolved state shared by Python lowering, integrations, and rendering."""

    service_plan: ServicePlan
    settings: PythonSettings
    symbols: PythonSymbolProvider
    protocol: PythonProtocol
    integrations: tuple[PythonIntegration, ...]
    dependencies: set[str] = field(default_factory=_empty_dependencies)


def create_generation_context(
    plan: ServicePlan,
    settings: PythonSettings,
    *,
    available_integrations: tuple[PythonIntegration, ...] = (),
    protocol_registry: PythonProtocolRegistry | None = None,
) -> PythonGenerationContext:
    """Activate configured hooks, then resolve symbols and an explicit service protocol."""
    integrations = select_integrations(available_integrations, settings.enabled_integrations)
    for integration in integrations:
        plan = integration.preprocess(plan, settings)

    symbols = PythonSymbolProvider(
        plan,
        package=settings.package,
        external_models=settings.external_models,
    )
    for integration in integrations:
        symbols = integration.decorate_symbol_provider(symbols)

    registry = (protocol_registry or PythonProtocolRegistry.default()).copy()
    for integration in integrations:
        for protocol in integration.protocols():
            registry.register(protocol)
    protocol = registry.resolve(plan.service.protocols, preference=settings.protocol_preference)
    context = PythonGenerationContext(plan, settings, symbols, protocol, integrations)
    for integration in integrations:
        integration.configure(context)
    return context


def _validate_import(module: str, symbol: str, owner: str) -> None:
    if not module or any(not segment.isidentifier() or keyword.iskeyword(segment) for segment in module.split(".")):
        msg = f"{owner} module {module!r} is not a valid Python module"
        raise ValueError(msg)
    if not symbol.isidentifier() or keyword.iskeyword(symbol):
        msg = f"{owner} symbol {symbol!r} is not a valid Python identifier"
        raise ValueError(msg)
