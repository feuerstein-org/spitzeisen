"""Explicit opt-in extension hooks for Python code generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spitzeisen.codegen.python_context import PythonGenerationContext, PythonSettings
    from spitzeisen.codegen.python_protocols import PythonProtocol
    from spitzeisen.codegen.python_symbols import PythonSymbolProvider
    from spitzeisen.codegen.service_plan import ServicePlan


class PythonIntegration:
    """Small target-native integration API; installation alone never activates it."""

    name: str

    def preprocess(self, plan: ServicePlan, settings: PythonSettings) -> ServicePlan:
        """Return a target view of the plan before symbols and protocols are resolved."""
        del settings
        return plan

    def decorate_symbol_provider(self, provider: PythonSymbolProvider) -> PythonSymbolProvider:
        """Wrap or replace the Python symbol provider."""
        return provider

    def protocols(self) -> tuple[PythonProtocol, ...]:
        """Return protocol handlers contributed by this integration."""
        return ()

    def configure(self, context: PythonGenerationContext) -> None:
        """Contribute dependencies or other renderer context after selection."""


def select_integrations(
    available: tuple[PythonIntegration, ...],
    enabled: tuple[str, ...],
) -> tuple[PythonIntegration, ...]:
    """Select integrations by explicit settings and preserve configuration order."""
    by_name: dict[str, PythonIntegration] = {}
    for integration in available:
        if integration.name in by_name:
            msg = f"duplicate Python integration name {integration.name!r}"
            raise ValueError(msg)
        by_name[integration.name] = integration
    missing = set(enabled).difference(by_name)
    if missing:
        msg = f"unknown enabled Python integrations: {sorted(missing)}"
        raise ValueError(msg)
    return tuple(by_name[name] for name in enabled)
