"""Turn the vendored OpenAPI Pydantic models into generator data."""

from .openapi import Endpoint, EndpointCollection, GeneratorData

__all__ = ["Endpoint", "EndpointCollection", "GeneratorData"]
