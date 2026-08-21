"""
Pytest plugin.

Registered as an entry point, so installing spitzeisen makes these fixtures available to any
client library's test suite without a conftest import.
"""

import pytest
from httpx2 import AsyncClient, Client

from spitzeisen._async.config import AsyncSpitzeisenConfig
from spitzeisen._sync.config import SyncSpitzeisenConfig
from spitzeisen.limits import NoLimit
from spitzeisen.testing.factory import MockApiFactory
from spitzeisen.testing.transport import FakeRouter

BASE_URL = "https://fake.test"


@pytest.fixture
def router() -> FakeRouter:
    """Scripted responses, and a log of the requests that were served."""
    return FakeRouter()


@pytest.fixture
def async_http_client(router: FakeRouter) -> AsyncClient:
    """An awaitable httpx2 client whose requests are answered by `router`."""
    return AsyncClient(transport=router.mock_transport())


@pytest.fixture
def sync_http_client(router: FakeRouter) -> Client:
    """A blocking httpx2 client whose requests are answered by `router`."""
    return Client(transport=router.mock_transport())


@pytest.fixture
def client_config(async_http_client: AsyncClient) -> AsyncSpitzeisenConfig:
    """
    A config wired to the scripted client with rate limiting disabled.

    Tests that care about limiter behaviour should build their own config with a real bucket;
    everything else runs without waiting for tokens.
    """
    return AsyncSpitzeisenConfig(
        base_url=BASE_URL,
        http_client=async_http_client,
        limiter=NoLimit(),
        max_retries=0,
    )


@pytest.fixture
def sync_client_config(sync_http_client: Client) -> SyncSpitzeisenConfig:
    """Blocking counterpart of `client_config`."""
    return SyncSpitzeisenConfig(
        base_url=BASE_URL,
        http_client=sync_http_client,
        limiter=NoLimit(),
        max_retries=0,
    )


@pytest.fixture
def mock_api_factory(mocker: object) -> MockApiFactory:
    """
    Build endpoint instances whose I/O helpers are stubbed.

    For testing endpoint logic — parameter coercion, validation, envelope handling — without
    involving the request path at all. Requires pytest-mock.
    """
    return MockApiFactory(mocker)
