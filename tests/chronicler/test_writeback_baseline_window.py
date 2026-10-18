"""The day-close write-back scores lane skew against the 28 days before the closed day.

Pins the window the write-back hands the personal-baseline scorer (bu-q7vx1q.80): the
closed day is the scored day, the band is the ``baseline_lookback_days`` days immediately
before it, and a baseline row for the closed day itself never feeds its own band.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from butlers.chronicler import storage, writeback
from butlers.chronicler.writeback import run_day_close_writeback

pytestmark = pytest.mark.unit

_DAY = date(2026, 7, 20)
_HOUR = 3600


def _row(day: date, seconds: int) -> SimpleNamespace:
    return SimpleNamespace(lane="work", local_date=day, seconds=seconds)


async def _skew_insights(monkeypatch: pytest.MonkeyPatch, offsets: list[int]) -> int:
    """Insights written for a 20h work day against 8h rows at ``_DAY - offset`` days."""

    async def day_rollups(_pool, *, local_date):
        return [_row(local_date, 20 * _HOUR)]

    async def range_rollups(_pool, *, start_date, end_date):
        return [
            _row(_DAY - timedelta(days=o), 8 * _HOUR)
            for o in offsets
            if start_date <= _DAY - timedelta(days=o) <= end_date
        ]

    async def no_flags(_pool, *, local_date):
        return []

    monkeypatch.setattr(storage, "list_daily_rollups", day_rollups)
    monkeypatch.setattr(storage, "list_daily_rollups_range", range_rollups)
    monkeypatch.setattr(storage, "list_daily_rollup_flags", no_flags)

    stored: list[dict] = []

    async def store_fact(**kwargs):
        stored.append(kwargs)

    result = await run_day_close_writeback(
        object(), day_date=_DAY, timezone="UTC", store_fact_fn=store_fact
    )
    assert result.errors == 0
    return sum(1 for fact in stored if fact["predicate"] == writeback.PREDICATE_LANE_SKEW)


async def test_the_band_spans_exactly_the_28_days_before_the_closed_day(monkeypatch):
    # Seven rows, including the oldest (day-28) and the newest (day-1) day of the window.
    assert await _skew_insights(monkeypatch, [1, 5, 9, 14, 20, 24, 28]) == 1


async def test_a_window_shifted_either_way_loses_a_row_and_reads_insufficient(monkeypatch):
    # day-29 is outside the 28-day window; with it replacing day-28 only six rows count.
    assert await _skew_insights(monkeypatch, [1, 5, 9, 14, 20, 24, 29]) == 0
    # The closed day itself is never a baseline row.
    assert await _skew_insights(monkeypatch, [0, 5, 9, 14, 20, 24, 28]) == 0
