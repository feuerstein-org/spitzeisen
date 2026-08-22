"""Structured diagnostics for OpenAPI compilation failures."""

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """The impact of a compiler diagnostic."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A stable URI and JSON Pointer identifying an OpenAPI value."""

    uri: str
    pointer: str = ""

    def __str__(self) -> str:
        """Render the location for a human-facing diagnostic."""
        return f"{self.uri}#{self.pointer}" if self.pointer else self.uri


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One actionable compiler message."""

    code: str
    message: str
    location: SourceLocation
    severity: Severity = Severity.ERROR

    def __str__(self) -> str:
        """Render a concise compiler-style message."""
        return f"{self.location}: {self.severity}: {self.code}: {self.message}"


class CodegenError(Exception):
    """A code-generation failure containing one or more precise diagnostics."""

    def __init__(self, *diagnostics: Diagnostic) -> None:
        """Store diagnostics and expose them through the regular exception message."""
        if not diagnostics:
            msg = "CodegenError requires at least one diagnostic"
            raise ValueError(msg)
        self.diagnostics = diagnostics
        super().__init__("\n".join(str(diagnostic) for diagnostic in diagnostics))


def error(code: str, message: str, location: SourceLocation) -> CodegenError:
    """Build a one-diagnostic compilation error."""
    return CodegenError(Diagnostic(code=code, message=message, location=location))
