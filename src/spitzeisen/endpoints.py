"""
The endpoint descriptor.

`SpitzeisenEndpointSpec` is the runtime half of the manifest: essentially details an OpenAPI
spec does not have (e.g. how much an API call costs in rate-limit tokens).
Generated endpoint modules declare one of these per endpoint, the core reads it and needs to know nothing else.
"""

from dataclasses import dataclass, field
from typing import Any

from spitzeisen.pagination import NoPagination, PaginationStrategy


@dataclass(frozen=True, slots=True)
class SpitzeisenEndpointSpec:
    """
    Everything the request path needs to know about one endpoint. Only GET method is supported currently.

    Args:
        path: Path template relative to the client's base URL, e.g. `/v1/records` or
            `/v1/records/{record_id}/history`. Placeholders are filled in by `url()`.
        pagination: How to walk this endpoint's collection, and where its records sit in the
            envelope. Defaults to a single page.
        cost: What one call draws from the rate limiter.

    """

    path: str
    pagination: PaginationStrategy = field(default_factory=NoPagination)
    cost: float = 1.0

    def url(self, base_url: str, **path_params: Any) -> str:
        """
        Build the absolute URL for a call, substituting any path placeholders.

        Raises KeyError naming the placeholder when a required path parameter is missing.
        """
        path = self.path.format(**path_params)
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"
