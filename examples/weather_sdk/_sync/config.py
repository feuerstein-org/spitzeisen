# Generated from weather_sdk/_async by build_sync.py -- do not edit.
"""OpenWeather credentials and transport configuration."""

from typing import Self

from pydantic import Field, model_validator

from spitzeisen import QueryParamAuth, SyncSpitzeisenConfig


class WeatherApiConfig(SyncSpitzeisenConfig):
    """
    OpenWeather credentials and request settings shared by a client.

    Pass this configuration as `config` when constructing a client. Reusing the same
    instance shares its HTTP connection and limiter.

    Args:
        api_key: Required, OpenWeather API key.
        base_url: API root URL. Defaults to `https://api.openweathermap.org`.
        limiter: Rate limiter compatible with this client surface. Defaults to `NoLimit`,
            which sends requests without rate limiting. The `single_bucket` and
            `sync_single_bucket` factories provide async and blocking limiters respectively.
            Using the `steindamm` library is recommended.
        max_retries: Maximum additional attempts after HTTP 429, HTTP 5xx, timeouts, or
            network errors. Defaults to 3, set to 0 to disable retries.
        request_timeout: HTTP timeout in seconds, applied to each request, including when
            using a supplied HTTP client. Defaults to 30, must be greater than zero.
        retry_backoff_base: Base delay in seconds for exponential retry backoff. Defaults
            to 1. Set this and `retry_backoff_floor` to 0 to retry without a delay.
        retry_backoff_floor: Minimum retry delay in seconds. Defaults to 1.
        on_validation_error: Default policy for response validation. "skip" (default)
            logs an invalid current weather or forecast model and returns None. "raise" raises
            `pydantic.ValidationError`. Both typed methods can override this policy per call.
        http_client: Optional httpx2 client , for example one using a custom transport.
            If omitted, a client is created on first use.
        owns_http_client: Whether the SDK closes a supplied HTTP client after the last
            context using this configuration exits. Defaults to False for supplied clients,
            clients created by the SDK are always owned by it.
        validate_inputs: Enables strict Pydantic checking through core's `validate_input`
            helper. Defaults to False.

    Raises:
        pydantic.ValidationError: If a configuration field has an invalid value, such as
            an empty API key, a negative retry count, or a non-positive request timeout.

    """

    base_url: str = "https://api.openweathermap.org"
    # Pydantic enforces this field's constraints when the config is constructed.
    api_key: str = Field(min_length=1, repr=False)

    @model_validator(mode="after")
    def configure_auth(self) -> Self:
        """Set `appid` authentication after Pydantic validates the configuration fields."""
        self.auth = QueryParamAuth("appid", self.api_key)
        return self
