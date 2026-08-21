"""
Test kit.

Available as a pytest plugin the moment spitzeisen is installed — no conftest wiring needed.
Import the pieces directly when a test wants to build them by hand.
"""

from spitzeisen.testing.factory import MockApiConfig, MockApiFactory
from spitzeisen.testing.transport import FakeRouter, RecordedRequest

__all__ = (
    "FakeRouter",
    "MockApiConfig",
    "MockApiFactory",
    "RecordedRequest",
)
