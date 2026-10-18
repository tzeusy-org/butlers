"""Unit tests for the pure balance/trends math in butlers.chronicler.balance
(IEA, tasks.md S9b, bu-jc6htw.2).

Pure-function tests only — no I/O, no mocked pool. API-level tests for
GET /balance, GET /trends, and GET /who-you-were-with live in
test_balance_trends_api.py and test_who_you_were_with_api.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from butlers.chronicler.aggregations import LANES
from butlers.chronicler.balance import (
    LANE_BASELINE_MIN_N,
    LaneBalance,
    compute_daily_balance,
    compute_lane_baseline,
    compute_lane_streak,
    is_lane_anomalous,
)

pytestmark = pytest.mark.unit

_DAY = date(2026, 7, 20)


def _series(values: list[int]) -> dict[date, int]:
    """Per-day seconds for the days immediately before ``_DAY`` (oldest first)."""
    return {_DAY - timedelta(days=len(values) - i): v for i, v in enumerate(values)}


# ---------------------------------------------------------------------------
# compute_lane_baseline
# ---------------------------------------------------------------------------


def test_baseline_mean_over_samples():
    result = compute_lane_baseline(
        {"work": _series([3600, 7200, 5400, 5400, 3600, 7200, 5400])}, local_date=_DAY
    )
    work = result["work"]
    assert work.center == pytest.approx(5400.0)
    assert work.n_observed == 7


def test_baseline_no_samples_returns_none_not_zero():
    result = compute_lane_baseline({}, local_date=_DAY)
    for lane in LANES:
        assert result[lane].center is None
        assert result[lane].n_observed == 0
        assert result[lane].status == "insufficient_history"


def test_baseline_covers_every_lane():
    result = compute_lane_baseline({"work": _series([3600])}, local_date=_DAY)
    assert set(result) == set(LANES)
    assert result["sleep"].center is None


# ---------------------------------------------------------------------------
# compute_daily_balance
# ---------------------------------------------------------------------------


def test_daily_balance_zero_fills_lane_with_no_activity_but_keeps_baseline():
    balances = compute_daily_balance({}, {"sleep": _series([28800, 25200] * 4)}, local_date=_DAY)
    by_lane = {b.lane: b for b in balances}
    sleep = by_lane["sleep"]
    assert sleep.seconds == 0
    assert sleep.baseline_seconds == pytest.approx(27000.0)
    assert sleep.delta_seconds == pytest.approx(-27000.0)
    assert sleep.baseline_sample_days == 8


def test_daily_balance_delta_positive_when_above_baseline():
    balances = compute_daily_balance(
        {"work": 36000}, {"work": _series([28800] * 7)}, local_date=_DAY
    )
    by_lane = {b.lane: b for b in balances}
    assert by_lane["work"].delta_seconds == pytest.approx(36000 - 28800)


def test_daily_balance_no_baseline_history_yields_null_delta():
    balances = compute_daily_balance({"work": 36000}, {}, local_date=_DAY)
    by_lane = {b.lane: b for b in balances}
    work = by_lane["work"]
    assert work.seconds == 36000
    assert work.baseline_seconds is None
    assert work.delta_seconds is None
    assert work.baseline_sample_days == 0


def test_daily_balance_covers_every_lane_sorted():
    balances = compute_daily_balance({}, {}, local_date=_DAY)
    assert [b.lane for b in balances] == sorted(LANES)


# ---------------------------------------------------------------------------
# compute_lane_streak
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "daily_seconds,expected",
    [
        ([], 0),
        ([0], 0),
        ([3600], 1),
        ([3600, 3600, 0], 0),
        ([0, 3600, 3600], 2),
        ([3600, 0, 3600, 3600], 2),
    ],
)
def test_streak_counts_trailing_nonzero_run(daily_seconds, expected):
    assert compute_lane_streak(daily_seconds) == expected


# ---------------------------------------------------------------------------
# is_lane_anomalous
# ---------------------------------------------------------------------------


def _lane(seconds: int, history: list[int]) -> LaneBalance:
    balances = compute_daily_balance({"work": seconds}, {"work": _series(history)}, local_date=_DAY)
    return next(b for b in balances if b.lane == "work")


def test_anomaly_requires_minimum_sample_days():
    lb = _lane(36000, [3600] * (LANE_BASELINE_MIN_N - 1))
    assert lb.state == "insufficient_history"
    assert is_lane_anomalous(lb) is False


def test_anomaly_requires_no_baseline_returns_false():
    lb = LaneBalance(
        lane="work",
        seconds=36000,
        baseline_seconds=None,
        delta_seconds=None,
        baseline_sample_days=0,
    )
    assert is_lane_anomalous(lb) is False


def test_anomaly_flags_large_absolute_and_relative_delta():
    lb = _lane(36000, [7200] * LANE_BASELINE_MIN_N)
    assert lb.state == "above"
    assert is_lane_anomalous(lb) is True


def test_anomaly_ignores_small_delta_below_absolute_floor():
    # Small baseline (10 min) with a tiny swing: inside the floored band even
    # though the ratio is huge.
    lb = _lane(660, [600] * LANE_BASELINE_MIN_N)
    assert lb.state == "within"
    assert is_lane_anomalous(lb) is False


def test_anomaly_ignores_small_relative_delta_on_large_baseline():
    # Large baseline (8h) with a one-hour swing: inside the band.
    lb = _lane(32400, [28800] * LANE_BASELINE_MIN_N)
    assert lb.state == "within"
    assert is_lane_anomalous(lb) is False
