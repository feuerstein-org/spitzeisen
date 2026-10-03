"""Failed batches finish cleaning up before the shared client can be closed."""

import asyncio

import pytest

from spitzeisen import gather_bounded


async def test_failure_drains_siblings_before_propagating() -> None:
    """A failure does not leave a sibling using the caller's client."""
    started = asyncio.Event()
    cleaned_up = asyncio.Event()
    error = ValueError("request failed")

    async def fail() -> None:
        await started.wait()
        raise error

    async def sibling() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            # Closing a response stream can itself need an event-loop turn.
            await asyncio.sleep(0)
            cleaned_up.set()

    with pytest.raises(ValueError, match="request failed") as caught:
        await gather_bounded(2, fail(), sibling())

    assert caught.value is error
    assert cleaned_up.is_set()


async def test_failure_cancels_a_future_waiting_for_a_slot() -> None:
    """A scheduled task is cancelled even when its wrapper has not acquired a slot."""
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def fail() -> None:
        await started.wait()
        msg = "request failed"
        raise ValueError(msg)

    async def sibling() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    scheduled = asyncio.create_task(sibling())
    with pytest.raises(ValueError, match="request failed"):
        await gather_bounded(1, fail(), scheduled)

    assert scheduled.cancelled()
    assert cleaned_up.is_set()
