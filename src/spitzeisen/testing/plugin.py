"""
Pytest fixtures

Enable with `pytest_plugins = ["spitzeisen.testing.plugin"]` in the root conftest.py.
Install `spitzeisen[testing]` for httpx2-pytest and pytest-asyncio.
"""

from collections.abc import AsyncIterator, Generator, Iterator
from typing import cast

import pytest
import pytest_asyncio
from pytest_httpx2 import HTTPXMock

from spitzeisen.testing.harness import ApiFactory, SyncApiFactory

pytest_plugins = ["pytest_httpx2"]

# Each test stores its own outcome under this key in item.stash.
_UNSUCCESSFUL = pytest.StashKey[bool]()


# NOTE: Pytest already reports failures, but still runs fixture teardown afterwards.
# The issue is that the teardown itself will produce an additional error since there is an
# "unused response". This wrapper avoids this by setting the _UNSUCCESSFUL flag.
# See _should_verify function.
@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Keep unused-response checks from obscuring a setup failure, test failure, or skip."""
    # Let pytest create the report before inspecting it.
    report = yield
    if report.when in {"setup", "call"} and not report.passed:
        item.stash[_UNSUCCESSFUL] = True
    return report


def _should_verify(request: pytest.FixtureRequest) -> bool:
    """Check whether test was successful and we should verify for leftover responses."""
    item = cast("pytest.Item", request.node)  # type: ignore[reportUnknownMemberType]
    # Only verify responses if this test was successful.
    return not item.stash.get(_UNSUCCESSFUL, False)


@pytest.fixture
def _http_verification(httpx2_mock: HTTPXMock, request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep HTTP mocking active through SDK teardown and preserve the original failure."""
    yield
    if not _should_verify(request):
        httpx2_mock.reset()


@pytest_asyncio.fixture
async def api_factory(_http_verification: None) -> AsyncIterator[ApiFactory]:
    """Build async SDKs and automatically verify their scripts and close their clients."""
    async with ApiFactory() as factory:
        yield factory


@pytest.fixture
def sync_api_factory(_http_verification: None) -> Iterator[SyncApiFactory]:
    """Build sync SDKs and automatically verify their scripts and close their clients."""
    with SyncApiFactory() as factory:
        yield factory
