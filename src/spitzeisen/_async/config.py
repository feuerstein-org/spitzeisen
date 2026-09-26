"""
Connection-level configuration for one client surface.

This module is transformed into the sync counterpart by unasync. Keep its wording neutral
and its behaviour mechanically convertible.
"""

from contextlib import nullcontext
from typing import Literal

from httpx2 import AsyncClient
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from spitzeisen.auth import AuthStrategy, NoAuth
from spitzeisen.limits import AsyncLimiter, NoLimit

ValidationMode = Literal["raise", "skip"]


class SpitzeisenConfig(BaseModel):
    """
    Connection-level settings shared by every operation of a client.

    Args:
        base_url: Root the operation paths are resolved against.
        auth: How credentials are applied to each request. Defaults to none.
        max_retries: Retries for retryable statuses and transport failures, with exponential
            backoff. 0 disables retrying.
        request_timeout: Per-request timeout in seconds, applied to owned and caller-supplied HTTP clients.
        retry_backoff_base: Multiplier for the exponential backoff. Set to 0 to retry immediately.
        retry_backoff_floor: Lower bound time between retries in seconds.
        on_validation_error: Default policy for `validate_record`, `validate_records`, and
            `get_models`. "raise" propagates `pydantic.ValidationError`; "skip" logs failures
            and returns None for one invalid record or drops invalid records from a list.
            `get_model` always raises validation errors.
        validate_inputs: Validate non-None arguments passed to ``validate_input`` with Pydantic strict mode before
            serialization. Off by default so Python-compatible values retain the permissive SDK
            behavior, enable it when boundary validation is more important than coercion.
        http_client: The httpx2 client for this surface. Supply one when custom transport
            behaviour is required, otherwise Spitzeisen creates and owns it lazily.
        owns_http_client: Whether Spitzeisen closes `http_client` automatically. Set for a client
            Spitzeisen built, or explicitly to hand ownership of a supplied client to it.
        limiter: A limiter for this surface. Defaults to `NoLimit`.

    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    base_url: str = Field(min_length=1)
    auth: AuthStrategy = Field(default_factory=NoAuth)
    max_retries: int = Field(default=3, ge=0)
    request_timeout: float = Field(default=30.0, gt=0)
    retry_backoff_base: float = Field(default=1.0, ge=0)
    retry_backoff_floor: float = Field(default=1.0, ge=0)
    on_validation_error: ValidationMode = "skip"
    validate_inputs: bool = False

    http_client: AsyncClient | None = None
    owns_http_client: bool = False
    limiter: AsyncLimiter = Field(default_factory=NoLimit)

    _refcount: int = PrivateAttr(default=0)
    _lock: nullcontext[None] = PrivateAttr(default_factory=nullcontext)

    # Lock is set to be a no-op in async, required for sync
    def get_http_client(self) -> AsyncClient:
        """Build the shared client once, including during concurrent first requests."""
        with self._lock:
            if self.http_client is None:
                self.http_client = AsyncClient(timeout=self.request_timeout)
                self.owns_http_client = True
            return self.http_client

    def acquire(self) -> None:
        """Register one more holder of the shared HTTP client."""
        with self._lock:
            self._refcount += 1

    def release(self) -> AsyncClient | None:
        """
        Deregister a holder and detach the owned client when its last holder exits.

        The caller closes the returned client themselves. New holders can create a fresh
        connection while the detached client finishes closing. A caller-supplied client stays
        attached and open unless its ownership was explicitly handed over.
        """
        # Lock is set to be a no-op in async, required for sync
        with self._lock:
            self._refcount = max(0, self._refcount - 1)
            if self._refcount or not self.owns_http_client:
                return None
            client = self.http_client
            self.http_client = None
            self.owns_http_client = False
            return client

    def backoff(self, attempt: int) -> float:
        """Return the exponential retry delay in seconds, subject to the configured floor."""
        return max(self.retry_backoff_floor, self.retry_backoff_base * float(2**attempt))
