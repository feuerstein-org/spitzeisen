"""Rate factory regressions for fractional periods and minimum expiry."""

import pytest

from spitzeisen import single_bucket, sync_single_bucket


@pytest.mark.parametrize("surface", ["async", "sync"])
@pytest.mark.parametrize(
    ("period_seconds", "refill_frequency", "expiry"),
    [(0.01, 0.01, 120), (0.25, 0.1, 120), (60, 0.1, 120), (60.01, 0.1, 121)],
)
def test_bucket_preserves_requested_rate_and_expiry(
    surface: str, period_seconds: float, refill_frequency: float, expiry: int
) -> None:
    """Preserve factory options, smooth refills, and rounded expiry above the 120-second floor."""
    factory = single_bucket if surface == "async" else sync_single_bucket
    bucket = factory("fractional-period", 10, period_seconds, max_sleep=7)

    assert bucket.name == "fractional-period"
    assert bucket.max_sleep == 7
    assert bucket.capacity == 10
    assert bucket.refill_amount / bucket.refill_frequency == pytest.approx(10 / period_seconds)
    assert bucket.refill_frequency == refill_frequency
    assert bucket.expiry == expiry
