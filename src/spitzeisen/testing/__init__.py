"""
SDK construction and cleanup for tests.

Enable `spitzeisen.testing.plugin` for factories with httpx2-pytest mocking.
Standalone factory context managers handle resources; supply HTTP mocking separately.
"""

from spitzeisen.testing.harness import ApiFactory, SyncApiFactory

__all__ = (
    "ApiFactory",
    "SyncApiFactory",
)
