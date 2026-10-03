"""Public harness behavior: real strategies, script verification, and resource ownership."""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Never, Self, assert_type, cast

import httpx2
import pytest
from pydantic import model_validator
from pytest_httpx2 import HTTPXMock

from spitzeisen import (
    BearerHeader,
    NoLimit,
    SpitzeisenApi,
    SpitzeisenConfig,
    SpitzeisenOperationSpec,
    SyncSpitzeisenApi,
    SyncSpitzeisenConfig,
)
from spitzeisen.testing import ApiFactory, SyncApiFactory

pytest_plugins = ["pytester"]

ITEMS = SpitzeisenOperationSpec("/items")


async def test_async_factory_preserves_config_and_rejects_reuse(httpx2_mock: HTTPXMock) -> None:
    """Typed construction preserves settings and custom HTTP; reused config cannot replace it."""

    class TenantConfig(SpitzeisenConfig):
        """An SDK's configuration with its own required setting."""

        tenant: str

    class TenantApi(SpitzeisenApi):
        """An SDK with a keyword-only constructor and a specific config type."""

        def __init__(self, *, config: TenantConfig) -> None:
            assert config.http_client is not None
            self.tenant = config.tenant
            super().__init__(config)

    limiter = NoLimit()
    config = TenantConfig(
        base_url="https://example.test",
        tenant="acme",
        auth=BearerHeader("secret"),
        limiter=limiter,
        max_retries=4,
        validate_inputs=True,
        on_validation_error="raise",
    )
    async with ApiFactory() as factory:
        client = await factory.create(
            TenantApi, config=config, http_client_factory=lambda: httpx2.AsyncClient(headers={"X-Test": "custom"})
        )
        assert_type(client, TenantApi)
        assert client.tenant == "acme"
        assert client.config is config
        assert config.limiter is limiter
        assert config.max_retries == 4
        assert config.validate_inputs
        assert config.on_validation_error == "raise"
        assert not config.owns_http_client
        http = config.http_client
        assert http is not None
        with pytest.raises(ValueError, match=r"fresh|http_client"):
            await factory.create(TenantApi, config=config)
        assert config.http_client is http
        httpx2_mock.add_response(
            url="https://example.test/items",
            match_headers={"Authorization": "Bearer secret", "X-Test": "custom"},
            json={"tenant": "acme"},
        )
        assert await client.get_json(ITEMS) == {"tenant": "acme"}
        assert not http.is_closed

    async with ApiFactory() as other_factory:
        with pytest.raises(ValueError, match=r"fresh|http_client"):
            await other_factory.create(TenantApi, config=config)
    assert config.http_client is http
    assert http.is_closed


def test_sync_factory_preserves_config_and_rejects_reuse(httpx2_mock: HTTPXMock) -> None:
    """Blocking factories preserve typed configuration and reject reuse before and after closure."""

    class TenantConfig(SyncSpitzeisenConfig):
        """An SDK's configuration with its own required setting."""

        tenant: str

    class TenantApi(SyncSpitzeisenApi):
        """An SDK with a keyword-only constructor and a specific config type."""

        def __init__(self, *, config: TenantConfig) -> None:
            assert config.http_client is not None
            self.tenant = config.tenant
            super().__init__(config)

    limiter = NoLimit()
    config = TenantConfig(
        base_url="https://example.test",
        tenant="acme",
        auth=BearerHeader("secret"),
        limiter=limiter,
        max_retries=4,
        validate_inputs=True,
        on_validation_error="raise",
    )
    with SyncApiFactory() as factory:
        client = factory.create(
            TenantApi, config=config, http_client_factory=lambda: httpx2.Client(headers={"X-Test": "custom"})
        )
        assert_type(client, TenantApi)
        assert client.tenant == "acme"
        assert client.config is config
        assert config.limiter is limiter
        assert config.max_retries == 4
        assert config.validate_inputs
        assert config.on_validation_error == "raise"
        assert not config.owns_http_client
        http = config.http_client
        assert http is not None
        with pytest.raises(ValueError, match=r"fresh|http_client"):
            factory.create(TenantApi, config=config)
        assert config.http_client is http
        httpx2_mock.add_response(
            url="https://example.test/items",
            match_headers={"Authorization": "Bearer secret", "X-Test": "custom"},
            json={"tenant": "acme"},
        )
        assert client.get_json(ITEMS) == {"tenant": "acme"}

    with SyncApiFactory() as other_factory, pytest.raises(ValueError, match=r"fresh|http_client"):
        other_factory.create(TenantApi, config=config)
    assert config.http_client is http
    assert http.is_closed


@pytest.mark.parametrize("owns_http", [False, True])
async def test_async_factory_rejects_supplied_http_without_taking_ownership(owns_http: bool) -> None:
    """Reject an existing connection before replacing it or taking responsibility for cleanup."""
    async with httpx2.AsyncClient() as existing, ApiFactory() as factory:
        config = SpitzeisenConfig(base_url="https://example.test", http_client=existing, owns_http_client=owns_http)
        with pytest.raises(ValueError, match=r"fresh|http_client"):
            await factory.create(SpitzeisenApi, config=config)
        assert config.http_client is existing
        assert config.owns_http_client is owns_http
        assert not existing.is_closed


@pytest.mark.parametrize("owns_http", [False, True])
def test_sync_factory_rejects_supplied_http_without_taking_ownership(owns_http: bool) -> None:
    """Rejecting an existing blocking connection leaves its ownership untouched."""
    with httpx2.Client() as existing, SyncApiFactory() as factory:
        config = SyncSpitzeisenConfig(base_url="https://example.test", http_client=existing, owns_http_client=owns_http)
        with pytest.raises(ValueError, match=r"fresh|http_client"):
            factory.create(SyncSpitzeisenApi, config=config)
        assert config.http_client is existing
        assert config.owns_http_client is owns_http
        assert not existing.is_closed


async def test_async_factory_rejects_active_config_without_http() -> None:
    """A lazily entered SDK's config is already in use even before it opens HTTP."""
    config = SpitzeisenConfig(base_url="https://example.test")
    async with SpitzeisenApi(config), ApiFactory() as factory:
        with pytest.raises(ValueError, match=r"fresh|active|in use"):
            await factory.create(SpitzeisenApi, config=config)
        assert config.http_client is None
        assert config._refcount == 1


def test_sync_factory_rejects_active_config_without_http() -> None:
    """A live blocking SDK's config must not gain another HTTP client unexpectedly."""
    config = SyncSpitzeisenConfig(base_url="https://example.test")
    with SyncSpitzeisenApi(config), SyncApiFactory() as factory:
        with pytest.raises(ValueError, match=r"fresh|active|in use"):
            factory.create(SyncSpitzeisenApi, config=config)
        assert config.http_client is None
        assert config._refcount == 1


async def test_async_factory_requires_external_http_ownership() -> None:
    """The factory owns the injected HTTP client, so SDK ownership must start disabled."""
    config = SpitzeisenConfig(base_url="https://example.test", owns_http_client=True)
    async with ApiFactory() as factory:
        with pytest.raises(ValueError, match=r"fresh|owns_http_client"):
            await factory.create(SpitzeisenApi, config=config)
        assert config.http_client is None
        assert config.owns_http_client


def test_sync_factory_requires_external_http_ownership() -> None:
    """Blocking injection rejects conflicting SDK ownership before allocating HTTP."""
    config = SyncSpitzeisenConfig(base_url="https://example.test", owns_http_client=True)
    with SyncApiFactory() as factory:
        with pytest.raises(ValueError, match=r"fresh|owns_http_client"):
            factory.create(SyncSpitzeisenApi, config=config)
        assert config.http_client is None
        assert config.owns_http_client


async def test_async_factory_rejects_sync_config() -> None:
    """Runtime validation catches the wrong config surface even for untyped callers."""
    config = SyncSpitzeisenConfig(base_url="https://example.test")
    async with ApiFactory() as factory:
        with pytest.raises(TypeError, match=r"[Cc]onfig"):
            await factory.create(SpitzeisenApi, config=cast("SpitzeisenConfig", config))
    assert config.http_client is None


def test_sync_factory_rejects_async_config() -> None:
    """The blocking factory cannot install a sync connection into async configuration."""
    config = SpitzeisenConfig(base_url="https://example.test")
    with SyncApiFactory() as factory, pytest.raises(TypeError, match=r"[Cc]onfig"):
        factory.create(SyncSpitzeisenApi, config=cast("SyncSpitzeisenConfig", config))
    assert config.http_client is None


@pytest.mark.parametrize("phase", ["constructor", "entry"])
async def test_async_setup_failure_preserves_existing_clients(httpx2_mock: HTTPXMock, phase: str) -> None:
    """Failed construction or entry closes only the new HTTP client without running SDK exit."""
    error = ValueError(f"{phase} failed")
    failed_config = SpitzeisenConfig(base_url="https://example.test")

    class FailingApi(SpitzeisenApi):
        """An SDK that fails before its context can be entered."""

        def __init__(self, config: SpitzeisenConfig) -> None:
            super().__init__(config)
            if phase == "constructor":
                raise error

        async def __aenter__(self) -> Never:
            raise error

        async def __aexit__(self, *args: object) -> None:
            pytest.fail("SDK exit ran despite failed context entry")

    async with ApiFactory() as factory:
        first = await factory.create(SpitzeisenApi, config=SpitzeisenConfig(base_url="https://example.test"))
        first_http = first.config.http_client
        assert first_http is not None

        with pytest.raises(ValueError, match=f"{phase} failed"):
            await factory.create(FailingApi, config=failed_config)

        assert failed_config.http_client is not None
        assert failed_config.http_client.is_closed
        assert not first_http.is_closed
        httpx2_mock.add_response(url="https://example.test/items", json={"still": "usable"})
        assert await first.get_json(ITEMS) == {"still": "usable"}

    assert first_http.is_closed


@pytest.mark.parametrize("phase", ["constructor", "entry"])
def test_sync_setup_failure_preserves_existing_clients(httpx2_mock: HTTPXMock, phase: str) -> None:
    """Blocking setup failures release their own resources while leaving earlier clients usable."""
    error = ValueError(f"{phase} failed")
    failed_config = SyncSpitzeisenConfig(base_url="https://example.test")

    class FailingApi(SyncSpitzeisenApi):
        """An SDK that fails before its context can be entered."""

        def __init__(self, config: SyncSpitzeisenConfig) -> None:
            super().__init__(config)
            if phase == "constructor":
                raise error

        def __enter__(self) -> Never:
            raise error

        def __exit__(self, *args: object) -> None:
            pytest.fail("SDK exit ran despite failed context entry")

    with SyncApiFactory() as factory:
        first = factory.create(SyncSpitzeisenApi, config=SyncSpitzeisenConfig(base_url="https://example.test"))
        first_http = first.config.http_client
        assert first_http is not None

        with pytest.raises(ValueError, match=f"{phase} failed"):
            factory.create(FailingApi, config=failed_config)

        assert failed_config.http_client is not None
        assert failed_config.http_client.is_closed
        assert not first_http.is_closed
        httpx2_mock.add_response(url="https://example.test/items", json={"still": "usable"})
        assert first.get_json(ITEMS) == {"still": "usable"}

    assert first_http.is_closed


async def test_async_builder_supports_config_and_closes_http_on_error(httpx2_mock: HTTPXMock) -> None:
    """Builders support HTTP-dependent config and clean up after a failing test."""
    clients: list[httpx2.AsyncClient] = []

    class HttpRequiredConfig(SpitzeisenConfig):
        """Configuration that cannot be created before HTTP is available."""

        @model_validator(mode="after")
        def require_http(self) -> Self:
            if self.http_client is None:
                msg = "HTTP must be available during configuration"
                raise ValueError(msg)
            return self

    class CustomApi(SpitzeisenApi):
        """An SDK whose constructor accepts dependencies directly."""

        def __init__(self, tenant: str, http: httpx2.AsyncClient) -> None:
            self.tenant = tenant
            super().__init__(HttpRequiredConfig(base_url="https://example.test", http_client=http))

    @asynccontextmanager
    async def build(http: httpx2.AsyncClient) -> AsyncGenerator[CustomApi]:
        clients.append(http)
        try:
            yield CustomApi("acme", http)
        finally:
            assert not http.is_closed

    with pytest.raises(ValueError, match="original failure"):  # noqa: PT012 - exercise context exit with an active error
        async with ApiFactory() as factory:
            client = await factory.from_builder(build)
            assert_type(client, CustomApi)
            assert client.tenant == "acme"
            httpx2_mock.add_response(url="https://example.test/items", json={"tenant": "acme"})
            assert await client.get_json(ITEMS) == {"tenant": "acme"}
            msg = "original failure"
            raise ValueError(msg)
    assert clients[0].is_closed


def test_sync_builder_supports_config_and_closes_http_on_error(httpx2_mock: HTTPXMock) -> None:
    """Blocking builders preserve custom construction and cleanup on test failure."""
    clients: list[httpx2.Client] = []

    class HttpRequiredConfig(SyncSpitzeisenConfig):
        """Configuration that cannot be created before HTTP is available."""

        @model_validator(mode="after")
        def require_http(self) -> Self:
            if self.http_client is None:
                msg = "HTTP must be available during configuration"
                raise ValueError(msg)
            return self

    class CustomApi(SyncSpitzeisenApi):
        """An SDK whose constructor accepts dependencies directly."""

        def __init__(self, tenant: str, http: httpx2.Client) -> None:
            self.tenant = tenant
            super().__init__(HttpRequiredConfig(base_url="https://example.test", http_client=http))

    @contextmanager
    def build(http: httpx2.Client) -> Generator[CustomApi]:
        clients.append(http)
        try:
            yield CustomApi("acme", http)
        finally:
            assert not http.is_closed

    with pytest.raises(ValueError, match="original failure"), SyncApiFactory() as factory:  # noqa: PT012
        client = factory.from_builder(build)
        assert_type(client, CustomApi)
        assert client.tenant == "acme"
        httpx2_mock.add_response(url="https://example.test/items", json={"tenant": "acme"})
        assert client.get_json(ITEMS) == {"tenant": "acme"}
        msg = "original failure"
        raise ValueError(msg)
    assert clients[0].is_closed


@pytest.mark.parametrize(
    ("phase", "outcome"),
    [("call", "pass"), ("call", "fail"), ("call", "skip"), ("setup", "fail"), ("setup", "skip")],
)
def test_plugin_verifies_only_successful_tests(pytester: pytest.Pytester, phase: str, outcome: str) -> None:
    """Only successful tests report unused mocks; early failures and skips keep their outcome."""
    action = {"pass": "pass", "fail": "pytest.fail('original failure')", "skip": "pytest.skip('intentional')"}[outcome]
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.fixture
        def ready(sync_api_factory, httpx2_mock):
            httpx2_mock.add_response(url="https://example.test/unused", json={{}})
            {action if phase == "setup" else "pass"}

        def test_example(ready):
            {action if phase == "call" else "pass"}
        """
    )
    result = pytester.runpytest_inprocess(
        "-p", "spitzeisen.testing.plugin", "-o", "asyncio_default_fixture_loop_scope=function", "-q"
    )
    result.assert_outcomes(
        passed=int(outcome == "pass"),
        failed=int(phase == "call" and outcome == "fail"),
        skipped=int(outcome == "skip"),
        errors=int(outcome == "pass" or (phase == "setup" and outcome == "fail")),
    )
    if outcome == "pass":
        assert "responses are mocked but not requested" in result.stdout.str()
    else:
        assert "responses are mocked but not requested" not in result.stdout.str()


@pytest.mark.parametrize("surface", ["async", "sync"])
def test_plugin_mocks_sdk_entry_and_teardown(pytester: pytest.Pytester, surface: str) -> None:
    """Factory fixture dependencies keep mocking active through SDK entry and finalization."""
    is_async = surface == "async"
    prefix = "" if is_async else "Sync"
    fixture = "api_factory" if is_async else "sync_api_factory"
    define = "async def" if is_async else "def"
    wait = "await " if is_async else ""
    enter = "__aenter__" if is_async else "__enter__"
    leave = "__aexit__" if is_async else "__exit__"
    pytester.makepyfile(
        f"""
        from spitzeisen import {prefix}SpitzeisenApi, {prefix}SpitzeisenConfig, SpitzeisenOperationSpec

        class EagerApi({prefix}SpitzeisenApi):
            {define} {enter}(self):
                assert {wait}self.get_json(SpitzeisenOperationSpec("/ready")) == {{"ready": True}}
                return {wait}super().{enter}()

            {define} {leave}(self, *args):
                assert {wait}self.get_json(SpitzeisenOperationSpec("/closing")) == {{"closed": True}}
                return {wait}super().{leave}(*args)

        {define} test_example({fixture}, httpx2_mock):
            httpx2_mock.add_response(url="https://example.test/ready", json={{"ready": True}})
            httpx2_mock.add_response(url="https://example.test/closing", json={{"closed": True}})
            client = {wait}{fixture}.create(
                EagerApi, config={prefix}SpitzeisenConfig(base_url="https://example.test", max_retries=0)
            )
            assert not client.config.http_client.is_closed
        """
    )
    result = pytester.runpytest_inprocess(
        "-p",
        "spitzeisen.testing.plugin",
        "-o",
        "asyncio_mode=auto",
        "-o",
        "asyncio_default_fixture_loop_scope=function",
        "-q",
    )
    result.assert_outcomes(passed=1, errors=0)
