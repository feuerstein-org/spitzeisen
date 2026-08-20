"""
Bounded-concurrency fan-out helpers.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

DEFAULT_LIMIT = 50


async def gather_bounded[T](limit: int = DEFAULT_LIMIT, *awaitables: Awaitable[T]) -> list[T]:
    """Run `awaitables` concurrently with at most `limit` in flight at once."""
    if limit < 1:
        msg = "limit must be >= 1"
        raise ValueError(msg)
    semaphore = asyncio.Semaphore(limit)

    async def _run(awaitable: Awaitable[T]) -> T:
        async with semaphore:
            return await awaitable

    return await asyncio.gather(*(_run(awaitable) for awaitable in awaitables))


def map_bounded[T, R](func: Callable[[T], R], items: Iterable[T], limit: int = DEFAULT_LIMIT) -> list[R]:
    """Run `func` over `items` on a bounded thread pool (limit)."""
    with ThreadPoolExecutor(max_workers=limit) as pool:
        return list(pool.map(func, items))
