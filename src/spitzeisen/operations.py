"""
The operation descriptor.

SDKs declare paths, per-request costs, HTTP methods, and pagination here.
The core reads this descriptor without knowing the vendor's domain or response types.
"""

from dataclasses import dataclass, field

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
        method: HTTP method, defaulting to GET.
        retryable: Override whether a request can be retried. By default only idempotent
            methods are retried. Enable for POST only when repeating the operation is safe.

    """

    path: str
    pagination: PaginationStrategy = field(default_factory=NoPagination)
    cost: float = 1.0
    method: str = "GET"
    retryable: bool | None = None

    def __post_init__(self) -> None:
        """Reject unsupported method names before making a request."""
        if self.method not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
            msg = f"Unsupported HTTP method: {self.method!r}"
            raise ValueError(msg)

    @property
    def can_retry(self) -> bool:
        """Whether replaying this operation is safe under the configured retry policy."""
        return (
            self.retryable if self.retryable is not None else self.method in {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}
        )

    def url(self, base_url: str, **path_params: str) -> str:
        """Build the URL from already serialized path params (use ``serialize_path_param``)."""
        path = self.path.format(**path_params)
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"
