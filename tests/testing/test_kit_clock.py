"""Injected clock state advances together and never leaks between owners."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from butlers.testing.clock import FakeClock

pytestmark = pytest.mark.unit


def test_clock_advances_utc_and_monotonic_from_independent_origins():
    instant = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
    first = FakeClock(instant, monotonic=12.5)
    second = FakeClock(instant)
    assert first.now() == instant.astimezone(UTC)
    assert first.now().tzinfo is UTC
    assert first.monotonic() == 12.5
    first.advance(0.25)
    first.advance(timedelta(seconds=2))
    assert first.now() == instant + timedelta(seconds=2.25)
    assert first.monotonic() == 14.75
    assert second.now() == instant
    assert second.monotonic() == 0.0
    assert FakeClock().now() == FakeClock().now()


@pytest.mark.parametrize("seconds", [-1, timedelta(microseconds=-1), float("nan"), float("inf")])
def test_clock_refuses_invalid_advance_without_changing_either_clock(seconds):
    clock = FakeClock()
    before = clock.now(), clock.monotonic()
    with pytest.raises(ValueError):
        clock.advance(seconds)
    assert (clock.now(), clock.monotonic()) == before


def test_clock_refuses_naive_datetime():
    with pytest.raises(ValueError, match="aware"):
        FakeClock(datetime(2026, 1, 1))
