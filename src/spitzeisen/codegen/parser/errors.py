"""Small structured diagnostics shared by import and Smithy parsing."""

from dataclasses import dataclass

__all__ = ["ParseError"]


@dataclass(frozen=True, slots=True)
class ParseError:
    """One non-fatal importer or Smithy frontend diagnostic."""

    detail: str | None = None
    header: str = "Unable to parse part of the Smithy model"
    data: object | None = None
