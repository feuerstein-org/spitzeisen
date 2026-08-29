"""Structured diagnostics emitted by external model importers."""

from dataclasses import dataclass

__all__ = ["ModelImportWarning"]


@dataclass(frozen=True, slots=True)
class ModelImportWarning:
    """One non-fatal model-import diagnostic."""

    detail: str | None = None
    header: str = "Unable to import part of the service model"
    data: object | None = None
