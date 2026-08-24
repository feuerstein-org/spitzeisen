"""
The operation descriptor.

`SpitzeisenOperationSpec` is the runtime half of the manifest: details an OpenAPI
operation does not have (e.g. how much an API call costs in rate-limit tokens).
Generated operation modules declare one of these per operation, and the core reads it
without needing to know anything else about the vendor API.
"""

from dataclasses import dataclass, field
from typing import Any

from spitzeisen.pagination import NoPagination, PaginationStrategy


@dataclass(frozen=True, slots=True)
class SpitzeisenOperationSpec:
    """
    Everything the request path needs to know about one API operation.

    Args:
        path: Path template relative to the client's base URL, e.g. `/v1/records` or
            `/v1/records/{record_id}/history`. Placeholders are filled in by :meth:`url`.
        pagination: How to walk this operation's collection, and where its records sit in the
            envelope. Defaults to a single page.
        cost: What one call draws from the rate limiter.

    """

    path: str
    pagination: PaginationStrategy = field(default_factory=NoPagination)
    cost: float = 1.0

    def url(self, base_url: str, **path_params: Any) -> str:
        """Build the absolute URL, substituting any required path params."""
        path = self.path.format(**path_params)
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"
