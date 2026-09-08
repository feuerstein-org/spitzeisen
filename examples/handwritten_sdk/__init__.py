"""An illustrative Massive-shaped SDK, separate from the actual massive-api package."""

from handwritten_sdk.reference import AsyncReferenceApi
from handwritten_sdk.splits import AsyncSplitsApi
from spitzeisen import AsyncSpitzeisenApi, AsyncSpitzeisenConfig

__all__ = ("AsyncMarketDataApi", "AsyncMarketDataConfig", "AsyncReferenceApi", "AsyncSplitsApi")


class AsyncMarketDataConfig(AsyncSpitzeisenConfig):
    """Vendor defaults augment the shared transport/authentication configuration."""

    base_url: str = "https://api.massive.com"


class AsyncMarketDataApi(AsyncSpitzeisenApi):
    """Expose typed endpoint groups using the runtime's cached shared-config instances."""

    @property
    def reference_api(self) -> AsyncReferenceApi:
        """Reference operations using this client's transport, auth, limiter, and policy."""
        return self.api(AsyncReferenceApi)

    @property
    def splits_api(self) -> AsyncSplitsApi:
        """Split operations sharing the same connection lifecycle."""
        return self.api(AsyncSplitsApi)
