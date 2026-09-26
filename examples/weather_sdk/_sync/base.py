# Generated from weather_sdk/_async/ by build_sync.py -- do not edit.
# Change the async module and run `python build_sync.py`.
"""API-key constructor shared by OpenWeather endpoint groups."""

from spitzeisen import SyncSpitzeisenApi
from weather_sdk._sync.config import WeatherApiConfig


class BaseWeatherApi(SyncSpitzeisenApi):
    """
    Shared constructor and connection handling for the client and endpoint groups.

    Pass an API key to use the default settings, or pass a configuration to control
    authentication, rate limiting, retries, and response validation. Instances given
    the same configuration share its HTTP client and limiter.

    Use a client context manager to manage the connection. The HTTP client is created
    on the first request and, when owned by the SDK, closes after the last context using
    that configuration exits. A supplied HTTP client stays open unless the configuration
    sets `owns_http_client=True`.
    """

    config: WeatherApiConfig

    def __init__(self, config: WeatherApiConfig | None = None, api_key: str | None = None) -> None:
        """
        Initialize an API client without making a network request.

        Args:
            config: Existing configuration for this client surface. Reuse the same
                object across endpoint groups to share the HTTP client and limiter.
                When supplied, this takes precedence over `api_key`.
            api_key: Non-empty OpenWeather API key used to create a default configuration
                when `config` is omitted. Ignored when `config` is supplied.

        Raises:
            ValueError: If neither a configuration nor a non-empty API key is supplied.
            pydantic.ValidationError: If the API key fails validation while creating
                the default configuration.

        """
        if config is None:
            if not api_key:
                msg = "Either config or api_key must be provided"
                raise ValueError(msg)
            config = WeatherApiConfig(api_key=api_key)
        super().__init__(config)
