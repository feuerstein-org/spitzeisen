"""
A factory for operation instances whose I/O is stubbed.

Operation classes are mostly param coercion, envelope handling and validation. This builds
a real instance of one with `_request` and `_get_all_pages` replaced, so those behaviours can
be tested without a session, a limiter or a URL in sight. It detects whether the class is
awaitable or blocking and stubs accordingly, so one test body can cover both surfaces.
"""

import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from spitzeisen._async.config import AsyncSpitzeisenConfig
from spitzeisen._sync.config import SyncSpitzeisenConfig


@dataclass
class MockApiConfig:
    """What the stubbed I/O helpers should return."""

    base_url: str = "https://fake.test"
    # Records returned by a stubbed `_get_all_pages` (collection operations).
    pages: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    # Decoded body returned by a stubbed `_request` (single-resource operations).
    result: Any = None


class MockApiFactory:
    """Create operation instances with their request path stubbed out."""

    def __init__(self, mocker: Any) -> None:
        """Bind pytest-mock's `mocker` fixture."""
        self.mocker = mocker

    def create[T](
        self,
        api_class: type[T],
        config: MockApiConfig | None = None,
        **kwargs: Any,
    ) -> tuple[T, SimpleNamespace]:
        """
        Build an instance of `api_class` with `_request` and `_get_all_pages` stubbed.

        `kwargs` are forwarded to `MockApiConfig` when no config is given. Returns the
        instance and a namespace holding the two stubs, so tests can assert on the arguments
        the operation passed down.
        """
        if config is None:
            config = MockApiConfig(**kwargs)

        is_async = inspect.iscoroutinefunction(getattr(api_class, "_request", None))
        client_config = (
            AsyncSpitzeisenConfig(base_url=config.base_url)
            if is_async
            else SyncSpitzeisenConfig(base_url=config.base_url)
        )
        instance = api_class(client_config)  # type: ignore[call-arg]

        make_mock = self.mocker.AsyncMock if is_async else self.mocker.MagicMock

        get_all_pages = make_mock(return_value=config.pages)
        request = make_mock(return_value=config.result)
        instance._get_all_pages = get_all_pages  # type: ignore[attr-defined]  # noqa: SLF001
        instance._request = request  # type: ignore[attr-defined]  # noqa: SLF001

        return instance, SimpleNamespace(get_all_pages=get_all_pages, request=request)
