"""Parser error structures adopted from openapi-python-client."""

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel

__all__ = ["ErrorLevel", "GeneratorError", "ParameterError", "ParseError", "PropertyError"]


class ErrorLevel(Enum):
    """The level of an error."""

    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class GeneratorError:
    """Information about an error which occurred during generation."""

    detail: str | None = None
    level: ErrorLevel = ErrorLevel.ERROR
    header: str = "Unable to generate the client"


@dataclass
class ParseError(GeneratorError):
    """An error encountered while parsing part of an OpenAPI document."""

    level: ErrorLevel = ErrorLevel.WARNING
    data: BaseModel | None = None
    header: str = "Unable to parse this part of your OpenAPI document: "


@dataclass
class PropertyError(ParseError):
    """An error encountered while creating a schema property."""

    header = "Problem creating a Property: "


@dataclass
class ParameterError(ParseError):
    """An error encountered while creating a parameter."""

    header = "Problem creating a Parameter: "
