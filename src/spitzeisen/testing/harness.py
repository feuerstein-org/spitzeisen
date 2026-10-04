"""SDK construction and cleanup, with matching async and sync factories."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager, AsyncExitStack, ExitStack
from types import TracebackType
from typing import Protocol, Self

import httpx2

from spitzeisen._async.config import SpitzeisenConfig
from spitzeisen._sync.config import SpitzeisenConfig as SyncSpitzeisenConfig


class _ClientConstructor[ConfigT, ContextT](Protocol):
    """A client constructor accepting its SDK-specific configuration by keyword."""

    def __call__(self, *, config: ConfigT) -> ContextT:
        """Construct the SDK context manager without entering it."""
        ...


def _check_fresh_config(config: object, expected_type: type[SpitzeisenConfig] | type[SyncSpitzeisenConfig]) -> None:
    """Check the config surface and reject existing HTTP dependencies or active SDK holders."""
    if not isinstance(config, expected_type):
        msg = "Config must match the factory's async or sync surface"
        raise TypeError(msg)
    if config.http_client is not None:
        msg = "Pass a fresh config without an http_client; the factory supplies its HTTP client"
        raise ValueError(msg)
    if config.owns_http_client:
        msg = "Pass a fresh config with owns_http_client=False; the factory owns its HTTP client"
        raise ValueError(msg)
    # Inspect core lifecycle state so the harness cannot change an active SDK's configuration.
    if config._refcount:  # type: ignore[reportPrivateUsage]  # noqa: SLF001
        msg = "Pass a fresh config that is not in use by an SDK context"
        raise ValueError(msg)


class ApiFactory:
    """
    Build and close SDKs using ordinary HTTP clients.

    Pass a client class and a fresh configuration to `create`, or use `from_builder`
    for custom construction. Both return the entered SDK with its concrete type.
    The matching pytest fixture enables `httpx2_mock` for scripting and verification.
    Outside pytest, the factory manages resources only; supply mocking separately.
    """

    def __init__(self) -> None:
        """Start an isolated group of clients whose resources will close together."""
        self._stack = AsyncExitStack()
        self._closed = False

    async def __aenter__(self) -> Self:
        """Use the factory without pytest."""
        self._check_open()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close SDKs before their HTTP clients, preserving an exception from the test."""
        self._closed = True
        await self._stack.__aexit__(exc_type, exc, traceback)

    async def create[ConfigT: SpitzeisenConfig, ClientT](
        self,
        client_class: _ClientConstructor[ConfigT, AbstractAsyncContextManager[ClientT]],
        *,
        config: ConfigT,
        http_client_factory: Callable[[], httpx2.AsyncClient] = httpx2.AsyncClient,
    ) -> ClientT:
        """
        Construct an async SDK from its class and a fresh configuration.

        `client_class` must accept `config=` and return an async context manager.
        Pass a new async `SpitzeisenConfig` or SDK-specific subclass, with no HTTP
        client, no active SDK contexts, and `owns_http_client=False`. The factory
        attaches HTTP to that same config before calling the SDK constructor,
        it preserves your authentication, pagination, limiter, and validation choices.
        Use a new config for every call, including after a failed construction.

        Example using pytest's `api_factory` fixture::

            from spitzeisen import SpitzeisenApi, SpitzeisenConfig

            client = await api_factory.create(
                SpitzeisenApi,
                config=SpitzeisenConfig(base_url="https://example.test"),
            )

        Substitute your SDK's API and config subclasses, the returned client retains its type.
        The factory manages context entry and cleanup. Script responses through the
        `httpx2_mock` fixture. Supply a custom HTTP client with
        `http_client_factory=YourHttpClient`, a callable taking no arguments.
        Use `from_builder` for constructors that require a custom build function.
        """
        self._check_open()
        _check_fresh_config(config, SpitzeisenConfig)

        def build(http: httpx2.AsyncClient) -> AbstractAsyncContextManager[ClientT]:
            # Custom HTTP context entry may yield; recheck before claiming this config.
            _check_fresh_config(config, SpitzeisenConfig)
            config.http_client = http
            return client_class(config=config)

        return await self.from_builder(build, http_client_factory=http_client_factory)

    async def from_builder[ClientT](
        self,
        build: Callable[[httpx2.AsyncClient], AbstractAsyncContextManager[ClientT]],
        *,
        http_client_factory: Callable[[], httpx2.AsyncClient] = httpx2.AsyncClient,
    ) -> ClientT:
        """
        Build an async SDK with a custom function or lambda receiving an HTTP client.

        Pass a regular function taking the supplied, entered `httpx2.AsyncClient` and
        returning an unentered async SDK context manager. Wire that HTTP client into
        your SDK's configuration. The factory calls `build(http)`, awaits SDK context
        entry, and manages cleanup, the builder itself is not an `async def`.

        Example when configuration must receive HTTP during construction::

            from spitzeisen import SpitzeisenApi, SpitzeisenConfig

            def build_api(http):
                return SpitzeisenApi(SpitzeisenConfig(base_url="https://example.test", http_client=http))

            client = await api_factory.from_builder(build_api)

        Pass the function itself, the factory supplies its argument. Use `create` for
        the usual class-and-config setup. You can supply a custom HTTP client with
        `http_client_factory=YourHttpClient`, a callable taking no arguments.
        """
        self._check_open()
        # Roll back this client's resources immediately if construction or entry fails.
        async with AsyncExitStack() as pending:
            http = await pending.enter_async_context(http_client_factory())
            sdk = build(http)
            client = await pending.enter_async_context(sdk)

            # Transfer cleanup without closing anything. Reverse order closes SDK before HTTP.
            cleanup = pending.pop_all()
            self._stack.push_async_exit(cleanup)

        return client

    async def aclose(self) -> None:
        """Close every SDK and HTTP client owned by this factory."""
        self._closed = True
        await self._stack.aclose()

    def _check_open(self) -> None:
        if self._closed:
            msg = "This API factory has already been closed"
            raise RuntimeError(msg)


# Keep the factories aligned, only the HTTP and context-manager operations differ.
class SyncApiFactory:
    """
    Build and close SDKs using ordinary HTTP clients.

    Pass a client class and a fresh configuration to `create`, or use `from_builder`
    for custom construction. Both return the entered SDK with its concrete type.
    The matching pytest fixture enables `httpx2_mock` for scripting and verification.
    Outside pytest, the factory manages resources only; supply mocking separately.
    """

    def __init__(self) -> None:
        """Start an isolated group of clients whose resources will close together."""
        self._stack = ExitStack()
        self._closed = False

    def __enter__(self) -> Self:
        """Use the factory without pytest."""
        self._check_open()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close SDKs before their HTTP clients, preserving an exception from the test."""
        self._closed = True
        self._stack.__exit__(exc_type, exc, traceback)

    def create[ConfigT: SyncSpitzeisenConfig, ClientT](
        self,
        client_class: _ClientConstructor[ConfigT, AbstractContextManager[ClientT]],
        *,
        config: ConfigT,
        http_client_factory: Callable[[], httpx2.Client] = httpx2.Client,
    ) -> ClientT:
        """
        Construct a sync SDK from its class and a fresh configuration.

        `client_class` must accept `config=` and return a sync context manager.
        Pass a new `SyncSpitzeisenConfig` or SDK-specific subclass, with no HTTP
        client, no active SDK contexts, and `owns_http_client=False`. The factory
        attaches HTTP to that same config before calling the SDK constructor,
        it preserves your authentication, pagination, limiter, and validation choices.
        Use a new config for every call, including after a failed construction.

        Example using pytest's `sync_api_factory` fixture::

            from spitzeisen import SyncSpitzeisenApi, SyncSpitzeisenConfig

            client = sync_api_factory.create(
                SyncSpitzeisenApi,
                config=SyncSpitzeisenConfig(base_url="https://example.test"),
            )

        Substitute your SDK's API and config subclasses, the returned client retains its type.
        The factory manages context entry and cleanup. Script responses through the
        `httpx2_mock` fixture. Supply a custom HTTP client with
        `http_client_factory=YourHttpClient`, a callable taking no arguments.
        Use `from_builder` for constructors that require a custom build function.
        """
        self._check_open()
        _check_fresh_config(config, SyncSpitzeisenConfig)

        def build(http: httpx2.Client) -> AbstractContextManager[ClientT]:
            # Custom HTTP context entry may yield; recheck before claiming this config.
            _check_fresh_config(config, SyncSpitzeisenConfig)
            config.http_client = http
            return client_class(config=config)

        return self.from_builder(build, http_client_factory=http_client_factory)

    def from_builder[ClientT](
        self,
        build: Callable[[httpx2.Client], AbstractContextManager[ClientT]],
        *,
        http_client_factory: Callable[[], httpx2.Client] = httpx2.Client,
    ) -> ClientT:
        """
        Build a sync SDK with a custom function or lambda receiving an HTTP client.

        Pass a regular function taking the supplied, entered `httpx2.Client` and
        returning an unentered sync SDK context manager. Wire that HTTP client into
        your SDK's configuration. The factory calls `build(http)`, enters the SDK
        context, and manages cleanup.

        Example when configuration must receive HTTP during construction::

            from spitzeisen import SyncSpitzeisenApi, SyncSpitzeisenConfig

            def build_api(http):
                return SyncSpitzeisenApi(
                    SyncSpitzeisenConfig(base_url="https://example.test", http_client=http)
                )

            client = sync_api_factory.from_builder(build_api)

        Pass the function itself, the factory supplies its argument. Use `create` for
        the usual class-and-config setup. You can supply a custom HTTP client with
        `http_client_factory=YourHttpClient`, a callable taking no arguments.
        """
        self._check_open()
        # Roll back this client's resources immediately if construction or entry fails.
        with ExitStack() as pending:
            http = pending.enter_context(http_client_factory())
            sdk = build(http)
            client = pending.enter_context(sdk)

            # Transfer cleanup without closing anything. Reverse order closes SDK before HTTP.
            cleanup = pending.pop_all()
            self._stack.push(cleanup)

        return client

    def close(self) -> None:
        """Close every SDK and HTTP client owned by this factory."""
        self._closed = True
        self._stack.close()

    def _check_open(self) -> None:
        if self._closed:
            msg = "This API factory has already been closed"
            raise RuntimeError(msg)
