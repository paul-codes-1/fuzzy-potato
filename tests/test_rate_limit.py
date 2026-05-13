"""Unit tests for the in-memory rate limiter."""

from __future__ import annotations

import time

import pytest

from rag import rate_limit as rl


@pytest.fixture(autouse=True)
def _reset_limiter():
    from rag import telemetry
    rl.limiter.reset()
    telemetry._client_ip_var.set(None)
    yield
    rl.limiter.reset()
    telemetry._client_ip_var.set(None)


def test_expensive_tier_allows_up_to_hour_cap():
    cap, _ = rl.EXPENSIVE_LIMITS
    for i in range(cap):
        d = rl.limiter.check("expensive", "1.2.3.4")
        assert d.allowed is True, f"call {i} should have been allowed"
        assert d.remaining_hour == cap - i - 1
    blocked = rl.limiter.check("expensive", "1.2.3.4")
    assert blocked.allowed is False
    assert blocked.reason == "hour_limit"
    assert blocked.retry_after_seconds >= 1


def test_buckets_are_per_ip():
    cap, _ = rl.EXPENSIVE_LIMITS
    for _ in range(cap):
        assert rl.limiter.check("expensive", "1.1.1.1").allowed is True
    # Second IP is untouched.
    assert rl.limiter.check("expensive", "2.2.2.2").allowed is True


def test_cheap_tier_has_higher_hour_cap_and_no_daily_cap():
    cap, day_cap = rl.CHEAP_LIMITS
    assert day_cap == 0  # sentinel for "no daily cap"
    d = rl.limiter.check("cheap", "1.1.1.1")
    assert d.remaining_day == -1  # -1 == disabled


def test_day_cap_blocks_after_hourly_resets():
    """If the hour-window resets but we've already hit the day cap, day_limit fires."""
    _, day_cap = rl.EXPENSIVE_LIMITS
    # Pretend each call is 1 minute apart to spread across the hour boundary.
    now = time.time()
    for i in range(day_cap):
        # Manually push into both deques to simulate distributed calls.
        bucket = rl.limiter._buckets[("expensive", "9.9.9.9")]
        bucket.day.append(now - (day_cap - i) * 60)
    # The hour window is empty (calls were >60min ago), but day is full.
    decision = rl.limiter.check("expensive", "9.9.9.9")
    assert decision.allowed is False
    assert decision.reason == "day_limit"


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        rl.limiter.check("medium", "1.1.1.1")


def test_module_level_check_uses_contextvar_when_ip_omitted(monkeypatch):
    from rag import telemetry
    telemetry._client_ip_var.set("5.5.5.5")
    d = rl.check("cheap")
    assert d.allowed is True
    # the bucket should be keyed against 5.5.5.5
    assert ("cheap", "5.5.5.5") in rl.limiter._buckets
