"""
Rate limiting.

spitzeisen requires something that can be entered as a context manager for a given cost.
Default bucket factories are provided. A client can supply a custom rate limiter by implementing
the protocol, using `steindamm` is recommended.
"""

from contextlib import AbstractAsyncContextManager, AbstractContextManager, nullcontext
from math import ceil
from typing import Any, Protocol, runtime_checkable

from steindamm import AsyncTokenBucket, SyncTokenBucket

# Refill in small increments so the request rate is smooth
REFILL_INTERVAL_SECONDS = 0.1


@runtime_checkable
class AsyncLimiter(Protocol):
    """Something that limits a request until capacity is available."""

    def __call__(self, cost: float, /) -> AbstractAsyncContextManager[object]:
        """Consume `cost` amount of tokens as a context manager."""
        ...


@runtime_checkable
class SyncLimiter(Protocol):
    """Something that limits a request until capacity is available."""

    def __call__(self, cost: float, /) -> AbstractContextManager[object]:
        """Consume `cost` amount of tokens as a context manager."""
        ...


class NoLimit:
    """
    An unlimited limiter, for APIs that publish no rate limit. The default.
    """

    def __call__(self, cost: float, /) -> nullcontext[None]:
        """Admit every request immediately."""
        return nullcontext()

    def __repr__(self) -> str:
        return "NoLimit()"


def async_single_bucket(
    name: str,
    requests_per_period: float,
    period_seconds: float,
    *,
    max_sleep: float = 60.0,
    connection: Any = None,
) -> AsyncTokenBucket:
    """
    Build the default awaitable limiter: one smoothly-refilling bucket.

    `requests_per_period` is the initial capacity, the bucket is refilled smoothly instead of at the
    end of `period_seconds`. Construct a custom AsyncLimiter for more control.

    Pass a redis connection to share the bucket across processes, without one it is in-memory.
    Bucket expires after `max(120, period_seconds * 2)` (gets cleared).
    """
    refill_interval = min(REFILL_INTERVAL_SECONDS, period_seconds)
    return AsyncTokenBucket.create(
        connection=connection,
        name=name,
        capacity=requests_per_period,
        refill_frequency=refill_interval,
        refill_amount=requests_per_period * (refill_interval / period_seconds),
        max_sleep=max_sleep,
        expiry=max(120, ceil(period_seconds * 2)),
    )


def sync_single_bucket(
    name: str,
    requests_per_period: float,
    period_seconds: float,
    *,
    max_sleep: float = 60.0,
    connection: Any = None,
) -> SyncTokenBucket:
    """
    Build the default blocking limiter: one smoothly-refilling bucket.

    `requests_per_period` is the initial capacity, the bucket is refilled smoothly instead of at the
    end of `period_seconds`. Construct a custom SyncLimiter for more control.

    Pass a redis connection to share the bucket across processes, without one it is in-memory.
    Bucket expires after `max(120, period_seconds * 2)` (gets cleared).
    """
    refill_interval = min(REFILL_INTERVAL_SECONDS, period_seconds)
    return SyncTokenBucket.create(
        connection=connection,
        name=name,
        capacity=requests_per_period,
        refill_frequency=refill_interval,
        refill_amount=requests_per_period * (refill_interval / period_seconds),
        max_sleep=max_sleep,
        expiry=max(120, ceil(period_seconds * 2)),
    )
