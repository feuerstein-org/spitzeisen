"""
Bounded-concurrency fan-out helpers.
"""

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

DEFAULT_LIMIT = 50


async def gather_bounded[T](limit: int = DEFAULT_LIMIT, *awaitables: Awaitable[T]) -> list[T]:
    """
    Run at most `limit` awaitables at once and return results in input order.

    On failure or cancellation, cancel and drain tasks before propagating the original
    error, so the caller can safely close a shared client.
    """
    if limit < 1:
        msg = "limit must be >= 1"
        raise ValueError(msg)
    semaphore = asyncio.Semaphore(limit)

    async def _run(awaitable: Awaitable[T]) -> T:
        async with semaphore:
            return await awaitable

    tasks = [asyncio.create_task(_run(awaitable)) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        # An input that contains a running coroutine may already run while its wrapper is waiting on the semaphore.
        futures = [value for value in awaitables if isinstance(value, asyncio.Future)]
        for task in (*tasks, *futures):
            task.cancel()
        await asyncio.gather(*tasks, *futures, return_exceptions=True)
        # A cancelled wrapper may never have entered the coroutine it was handed.
        for awaitable in awaitables:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
        raise


def map_bounded[T, R](func: Callable[[T], R], items: Iterable[T], limit: int = DEFAULT_LIMIT) -> list[R]:
    """Run `func` over `items` on a bounded thread pool (limit)."""
    with ThreadPoolExecutor(max_workers=limit) as pool:
        return list(pool.map(func, items))
