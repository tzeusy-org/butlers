"""Unit tests for the pure balance/trends math in butlers.chronicler.balance
(IEA, tasks.md S9b, bu-jc6htw.2).

Pure-function tests only — no I/O, no mocked pool. API-level tests for
GET /balance, GET /trends, and GET /who-you-were-with live in
test_balance_trends_api.py and test_who_you_were_with_api.py.
"""

from __future__ import annotations

import pytest

from butlers.chronicler.aggregations import LANES
from butlers.chronicler.balance import (
    ANOMALY_MIN_DELTA_SECONDS,
    ANOMALY_MIN_SAMPLE_DAYS,
    LaneBalance,
    compute_daily_balance,
    compute_lane_baseline,
    compute_lane_streak,
    is_lane_anomalous,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# compute_lane_baseline
# ---------------------------------------------------------------------------


def test_baseline_mean_over_samples():
    result = compute_lane_baseline({"work": [3600, 7200, 5400]})
    baseline_seconds, sample_days = result["work"]
    assert baseline_seconds == pytest.approx(5400.0)
    assert sample_days == 3


def test_baseline_no_samples_returns_none_not_zero():
    result = compute_lane_baseline({})
    for lane in LANES:
        baseline_seconds, sample_days = result[lane]
        assert baseline_seconds is None
        assert sample_days == 0


def test_baseline_covers_every_lane():
    result = compute_lane_baseline({"work": [3600]})
    assert set(result) == set(LANES)
    assert result["sleep"] == (None, 0)


# ---------------------------------------------------------------------------
# compute_daily_balance
# ---------------------------------------------------------------------------


def test_daily_balance_zero_fills_lane_with_no_activity_but_keeps_baseline():
    balances = compute_daily_balance({}, {"sleep": [28800, 25200]})
    by_lane = {b.lane: b for b in balances}
    sleep = by_lane["sleep"]
    assert sleep.seconds == 0
    assert sleep.baseline_seconds == pytest.approx(27000.0)
    assert sleep.delta_seconds == pytest.approx(-27000.0)
    assert sleep.baseline_sample_days == 2


def test_daily_balance_delta_positive_when_above_baseline():
    balances = compute_daily_balance({"work": 36000}, {"work": [28800, 28800]})
    by_lane = {b.lane: b for b in balances}
    assert by_lane["work"].delta_seconds == pytest.approx(36000 - 28800)


def test_daily_balance_no_baseline_history_yields_null_delta():
    balances = compute_daily_balance({"work": 36000}, {})
    by_lane = {b.lane: b for b in balances}
    work = by_lane["work"]
    assert work.seconds == 36000
    assert work.baseline_seconds is None
    assert work.delta_seconds is None
    assert work.baseline_sample_days == 0


def test_daily_balance_covers_every_lane_sorted():
    balances = compute_daily_balance({}, {})
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


def test_anomaly_requires_minimum_sample_days():
    lb = LaneBalance(
        lane="work",
        seconds=36000,
        baseline_seconds=3600.0,
        delta_seconds=32400.0,
        baseline_sample_days=ANOMALY_MIN_SAMPLE_DAYS - 1,
    )
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
    lb = LaneBalance(
        lane="work",
        seconds=36000,
        baseline_seconds=7200.0,
        delta_seconds=28800.0,
        baseline_sample_days=ANOMALY_MIN_SAMPLE_DAYS,
    )
    assert is_lane_anomalous(lb) is True


def test_anomaly_ignores_small_delta_below_absolute_floor():
    # Small baseline (10 min) with a tiny absolute swing — below
    # ANOMALY_MIN_DELTA_SECONDS even though the ratio is huge.
    lb = LaneBalance(
        lane="rest",
        seconds=660,
        baseline_seconds=600.0,
        delta_seconds=60.0,
        baseline_sample_days=ANOMALY_MIN_SAMPLE_DAYS,
    )
    assert lb.delta_seconds < ANOMALY_MIN_DELTA_SECONDS
    assert is_lane_anomalous(lb) is False


def test_anomaly_ignores_small_relative_delta_on_large_baseline():
    # Large baseline (8h) with a swing that clears the absolute floor but not
    # the relative floor.
    lb = LaneBalance(
        lane="work",
        seconds=32400,
        baseline_seconds=28800.0,
        delta_seconds=3600.0,
        baseline_sample_days=ANOMALY_MIN_SAMPLE_DAYS,
    )
    assert is_lane_anomalous(lb) is False
