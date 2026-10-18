"""Chronicles lane balance runs on the core personal-baseline scorer (bu-q7vx1q.80).

The pure seam is ``butlers.chronicler.balance``: a lane's "usual" is the median and
MAD-derived band from ``butlers.core.baselines`` over the lookback days before the scored
day, with an honest sample count and an ``insufficient_history`` state, not a trailing mean
with hand-tuned floors.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from butlers.chronicler import balance
from butlers.chronicler.balance import (
    LANE_BASELINE_K_MAD,
    LANE_BASELINE_MIN_N,
    LANE_MIN_BAND_HALF_WIDTH_SECONDS,
    LaneBalance,
    compute_daily_balance,
    is_lane_anomalous,
    lane_metric,
)
from butlers.core import baselines

pytestmark = pytest.mark.unit

_DAY = date(2026, 7, 20)
_HOUR = 3600


def _history(values: list[int], *, skip: frozenset[int] = frozenset()) -> dict[date, int]:
    """Per-day seconds for the days before ``_DAY`` (index 0 is the oldest).

    ``skip`` lists indexes left out, standing in for days with no materialized rollup.
    """
    return {
        _DAY - timedelta(days=len(values) - i): v for i, v in enumerate(values) if i not in skip
    }


def _work(seconds: int, history: dict[date, int], *, lookback_days: int = 28) -> LaneBalance:
    balances = compute_daily_balance(
        {"work": seconds}, {"work": history}, local_date=_DAY, lookback_days=lookback_days
    )
    return next(b for b in balances if b.lane == "work")


def test_a_spike_in_the_window_does_not_drag_the_median_center():
    ordinary = [8 * _HOUR + (i % 3) * 600 for i in range(27)]
    with_spike = [*ordinary[:10], 10 * _HOUR, *ordinary[10:]]  # 27 ordinary + one 10h day
    history = _history(with_spike)

    work = _work(18 * _HOUR, history)

    ordinary_median = sorted(ordinary)[len(ordinary) // 2]
    assert work.baseline_seconds == ordinary_median
    assert work.baseline_seconds != pytest.approx(sum(with_spike) / len(with_spike), abs=1)
    assert work.baseline_sample_days == 28


def test_the_spike_day_itself_scores_above_its_band():
    history = _history([8 * _HOUR] * 28)

    work = _work(18 * _HOUR, history)

    assert work.state == "above"
    assert is_lane_anomalous(work) is True
    assert work.mad_units == pytest.approx(
        (10 * _HOUR) / (LANE_MIN_BAND_HALF_WIDTH_SECONDS / LANE_BASELINE_K_MAD)
    )
    assert work.band_low is not None and work.band_high is not None
    assert work.band_high == pytest.approx(8 * _HOUR + LANE_MIN_BAND_HALF_WIDTH_SECONDS)


def test_a_drop_below_the_band_scores_below():
    work = _work(0, _history([8 * _HOUR] * 14))

    assert work.state == "below"
    assert is_lane_anomalous(work) is True


def test_fewer_than_min_n_days_is_insufficient_history_never_an_anomaly():
    work = _work(20 * _HOUR, _history([_HOUR] * (LANE_BASELINE_MIN_N - 1)))

    assert work.state == "insufficient_history"
    assert work.baseline_seconds is None
    assert work.delta_seconds is None
    assert work.baseline_sample_days == LANE_BASELINE_MIN_N - 1
    assert is_lane_anomalous(work) is False


def test_exactly_min_n_days_is_enough_to_score():
    work = _work(20 * _HOUR, _history([_HOUR] * LANE_BASELINE_MIN_N))

    assert work.state == "above"


def test_a_near_zero_lane_does_not_flag_minute_level_noise():
    work = _work(20 * 60, _history([60] * 14))  # 1 minute usual, 20 minutes today

    assert work.state == "within"
    assert is_lane_anomalous(work) is False


def test_missing_rollup_days_lower_coverage_and_read_insufficient_not_within():
    history = _history([8 * _HOUR] * 28, skip=frozenset(range(24)))  # only 4 of 28 days

    work = _work(8 * _HOUR, history)

    assert work.baseline_sample_days == 4
    assert work.state == "insufficient_history"


def test_days_outside_the_lookback_window_are_ignored():
    inside = _history([8 * _HOUR] * 10)
    stale = {_DAY - timedelta(days=100 + i): 0 for i in range(20)}

    work = _work(8 * _HOUR, {**inside, **stale}, lookback_days=10)

    assert work.baseline_sample_days == 10
    assert work.baseline_seconds == 8 * _HOUR


def test_the_scored_day_is_not_part_of_its_own_band():
    history = {**_history([8 * _HOUR] * 14), _DAY: 20 * _HOUR}

    work = _work(20 * _HOUR, history)

    assert work.baseline_seconds == 8 * _HOUR
    assert work.baseline_sample_days == 14


def test_a_short_lookback_caps_min_n_at_the_window():
    metric = lane_metric("work", 3)

    assert metric.min_n == 3
    assert _work(8 * _HOUR, _history([8 * _HOUR] * 3), lookback_days=3).state == "within"


def test_the_result_is_deterministic():
    history = _history([7 * _HOUR, 9 * _HOUR] * 7)

    assert _work(8 * _HOUR, history) == _work(8 * _HOUR, history)


def test_balance_is_wired_to_the_core_scorer_not_a_local_mean():
    assert balance.compute_baselines is baselines.compute_baselines
    assert balance.score is baselines.score
    assert not hasattr(balance, "ANOMALY_MIN_RATIO")
    assert not hasattr(balance, "ANOMALY_MIN_SAMPLE_DAYS")
