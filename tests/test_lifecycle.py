"""Shared clients remain usable and owned across concurrent lifecycle transitions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx2
import pytest

from spitzeisen import (
    SpitzeisenApi,
    SpitzeisenConfig,
    SpitzeisenOperationSpec,
    SyncSpitzeisenApi,
    SyncSpitzeisenConfig,
)

RECORDS = SpitzeisenOperationSpec("/records")


def _response(_request: httpx2.Request) -> httpx2.Response:
    """Serve successful requests without opening sockets."""
    return httpx2.Response(200, json={"ok": True})


def test_parallel_first_requests_share_one_owned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second request waits for construction instead of allocating an unowned connection."""
    constructing = Event()
    finish_construction = Event()
    second_started = Event()
    created: list[httpx2.Client] = []

    def create_client(*, timeout: float) -> httpx2.Client:
        client = httpx2.Client(transport=httpx2.MockTransport(_response), timeout=timeout)
        created.append(client)
        constructing.set()
        assert finish_construction.wait(5)
        return client

    monkeypatch.setattr("spitzeisen._sync.config.Client", create_client)
    monkeypatch.setattr("spitzeisen._sync.core.Client", create_client)
    api = SyncSpitzeisenApi(SyncSpitzeisenConfig(base_url="https://fake.test"))

    def second_request() -> object:
        second_started.set()
        return api.get_json(RECORDS)

    try:
        with api, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(api.get_json, RECORDS)
            try:
                assert constructing.wait(5)
                second = pool.submit(second_request)
                assert second_started.wait(5)
                # The constructor stays blocked while the other thread attempts its first request.
                with pytest.raises(TimeoutError):
                    second.result(timeout=0.1)
                assert len(created) == 1
            finally:
                finish_construction.set()
            assert first.result(timeout=5) == {"ok": True}
            assert second.result(timeout=5) == {"ok": True}
        assert len(created) == 1
        assert created[0].is_closed
        assert api.config.http_client is None
    finally:
        for client in created:
            client.close()


def test_new_sync_holder_can_request_while_previous_connection_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing a detached connection never blocks or clears a newer holder's connection."""
    closing = Event()
    finish_close = Event()

    class SlowCloseTransport(httpx2.MockTransport):
        def close(self) -> None:
            closing.set()
            assert finish_close.wait(5)

    previous = httpx2.Client(transport=SlowCloseTransport(_response))
    replacement = httpx2.Client(transport=httpx2.MockTransport(_response))

    def create_replacement(**_kwargs: object) -> httpx2.Client:
        return replacement

    monkeypatch.setattr("spitzeisen._sync.config.Client", create_replacement)
    monkeypatch.setattr("spitzeisen._sync.core.Client", create_replacement)
    config = SyncSpitzeisenConfig(base_url="https://fake.test", http_client=previous, owns_http_client=True)
    first = SyncSpitzeisenApi(config)
    second = SyncSpitzeisenApi(config)
    first.__enter__()
    with ThreadPoolExecutor(max_workers=2) as pool:
        exiting = pool.submit(first.__exit__)
        try:
            assert closing.wait(5)
            with second:
                assert second.get_json(RECORDS) == {"ok": True}
                assert config.http_client is replacement
                finish_close.set()
                exiting.result(timeout=5)
                assert config.http_client is replacement
                assert not replacement.is_closed
        finally:
            finish_close.set()
            exiting.result(timeout=5)
            replacement.close()
    assert replacement.is_closed
    assert config.http_client is None


async def test_new_async_holder_can_request_while_previous_connection_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Awaiting old connection cleanup does not expose a closed client to a new context."""
    closing = asyncio.Event()
    finish_close = asyncio.Event()

    class SlowCloseTransport(httpx2.MockTransport):
        async def aclose(self) -> None:
            closing.set()
            await asyncio.wait_for(finish_close.wait(), timeout=5)

    previous = httpx2.AsyncClient(transport=SlowCloseTransport(_response))
    replacement = httpx2.AsyncClient(transport=httpx2.MockTransport(_response))

    def create_replacement(**_kwargs: object) -> httpx2.AsyncClient:
        return replacement

    monkeypatch.setattr("spitzeisen._async.config.AsyncClient", create_replacement)
    monkeypatch.setattr("spitzeisen._async.core.AsyncClient", create_replacement)
    config = SpitzeisenConfig(base_url="https://fake.test", http_client=previous, owns_http_client=True)
    first = SpitzeisenApi(config)
    second = SpitzeisenApi(config)
    await first.__aenter__()
    exiting = asyncio.create_task(first.__aexit__())
    try:
        await asyncio.wait_for(closing.wait(), timeout=5)
        async with second:
            assert await second.get_json(RECORDS) == {"ok": True}
            assert config.http_client is replacement
            finish_close.set()
            await asyncio.wait_for(exiting, timeout=5)
            assert config.http_client is replacement
            assert not replacement.is_closed
    finally:
        finish_close.set()
        await exiting
        await replacement.aclose()
    assert replacement.is_closed
    assert config.http_client is None
