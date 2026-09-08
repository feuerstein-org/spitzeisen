# Generated from src/spitzeisen/_async/ by build_sync.py -- do not edit.
# Change the async module and run `mise run build-sync`.
"""
Connection-level configuration for one client surface.

This module is transformed into the sync counterpart by unasync. Keep its wording neutral
and its behaviour mechanically convertible.
"""

from typing import Literal

from httpx2 import Client
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from spitzeisen.auth import AuthStrategy, NoAuth
from spitzeisen.limits import NoLimit, SyncLimiter

ValidationMode = Literal["raise", "skip"]


class SyncSpitzeisenConfig(BaseModel):
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
        on_validation_error: Default behaviour of validated list methods. "raise" propagates
            `pydantic.ValidationError` with all invalid record indices; "skip" drops and logs them.
        strict_inputs: Validate non-None arguments passed to ``validate_input`` with Pydantic strict mode before
            serialization. Off by default so Python-compatible values retain the permissive SDK
            behavior; enable it when boundary validation is more important than coercion.
        strict_response_validation: Enforce modeled response constraints and known enum values.
            Off by default for API compatibility. Applies to nested models using ``response_constraints`` too;
            does not change parsing, member optionality, or raw response methods.
        http_client: The httpx2 client for this surface. Supply one when custom transport
            behaviour is required; otherwise Spitzeisen creates and owns it lazily.
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
    strict_inputs: bool = False
    strict_response_validation: bool = False

    http_client: Client | None = None
    owns_http_client: bool = False
    limiter: SyncLimiter = Field(default_factory=NoLimit)

    _refcount: int = PrivateAttr(default=0)

    def acquire(self) -> None:
        """Register one more holder of the shared HTTP client."""
        self._refcount += 1

    def release(self) -> bool:
        """
        Deregister a holder and report whether the HTTP client should now be closed.

        True only when the last holder has exited and the client is Spitzeisen's to close: one
        that was never built stays None, and one the caller supplied remains open unless the
        caller explicitly handed over ownership.
        """
        self._refcount = max(0, self._refcount - 1)
        return self._refcount == 0 and self.http_client is not None and self.owns_http_client

    def backoff(self, attempt: int) -> float:
        """Return the exponential retry delay in seconds, subject to the configured floor."""
        return max(self.retry_backoff_floor, self.retry_backoff_base * float(2**attempt))
