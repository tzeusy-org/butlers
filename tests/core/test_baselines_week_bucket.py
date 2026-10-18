"""The ``week`` bucket of the personal-baseline scorer (bu-q7vx1q.94.2).

A week sample is keyed by the date its week starts on; the caller picks the anchor weekday.
The ``day`` and ``dow`` buckets must not move.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from butlers.core.baselines import (
    BaselineMetric,
    BaselineState,
    Deviation,
    EpisodeAction,
    bucket_value,
    compute_baselines,
    episode_decision,
    window_bounds,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 13, 6, tzinfo=UTC)
_MONDAY = date(2026, 10, 12)  # the start of the scored week

_WEEK = BaselineMetric(
    key="energy.total",
    unit="kWh",
    bucket="week",
    window_days=56,
    min_n=4,
    min_coverage=0.5,
    k_mad=2.6,
    k_consecutive=1,
    concern="above",
    min_dispersion=0.5,
    expected_signal_key="home:baseline:energy.total",
    refresh_interval=timedelta(days=7),
)


def _weekly(values: list[float]) -> dict[date, float]:
    """Samples for the weeks ending just before ``_MONDAY``, oldest first."""
    first = _MONDAY - timedelta(days=7 * len(values))
    return {first + timedelta(days=7 * i): v for i, v in enumerate(values)}


def test_a_week_window_is_window_days_over_seven_weekly_anchors() -> None:
    first, last = window_bounds(_WEEK, _MONDAY)

    assert last == _MONDAY - timedelta(days=7)
    assert first == last - timedelta(days=49)
    assert (last - first).days // 7 + 1 == 8


def test_a_week_baseline_expects_one_sample_per_week() -> None:
    (baseline,) = compute_baselines(_WEEK, {}, as_of=_MONDAY, now=_NOW)

    assert baseline.bucket_value == "all"
    assert baseline.n_expected == 8
    assert baseline.status == "insufficient_history"


def test_six_weeks_of_history_give_a_ready_band_around_the_median() -> None:
    samples = _weekly([20, 22, 21, 19, 23, 21])

    (baseline,) = compute_baselines(_WEEK, samples, as_of=_MONDAY, now=_NOW)

    assert baseline.status == "ready"
    assert (baseline.n_observed, baseline.n_expected) == (6, 8)
    assert baseline.center == 21
    assert baseline.band is not None and baseline.band[0] < 21 < baseline.band[1]


def test_three_weeks_are_below_min_n() -> None:
    (baseline,) = compute_baselines(_WEEK, _weekly([20, 22, 21]), as_of=_MONDAY, now=_NOW)

    assert baseline.status == "insufficient_history"
    assert baseline.center is None and baseline.n_observed == 3


def test_a_daily_keyed_sample_does_not_count_toward_a_week_window() -> None:
    off_anchor = {_MONDAY - timedelta(days=7 * i + 3): 21.0 for i in range(1, 7)}

    (baseline,) = compute_baselines(_WEEK, off_anchor, as_of=_MONDAY, now=_NOW)

    assert baseline.n_observed == 0


def test_a_week_inside_an_episode_is_left_out_of_the_denominator() -> None:
    excluded = {_MONDAY - timedelta(days=14)}

    (baseline,) = compute_baselines(
        _WEEK, _weekly([20, 22, 21, 19, 23, 21]), as_of=_MONDAY, now=_NOW, excluded_days=excluded
    )

    assert baseline.n_expected == 7


def test_window_days_must_be_whole_weeks() -> None:
    with pytest.raises(ValueError, match="divisible by 7"):
        replace(_WEEK, window_days=60)


def test_an_episode_opens_on_consecutive_weeks_not_days() -> None:
    metric = replace(_WEEK, k_consecutive=2)
    above = Deviation(BaselineState.ABOVE, 4.0, 6, 8, 0.75, True)
    weeks = {_MONDAY: above, _MONDAY - timedelta(days=7): above}
    days = {_MONDAY: above, _MONDAY - timedelta(days=1): above}

    opened = episode_decision(metric, weeks, as_of=_MONDAY, open_episode=None)
    not_opened = episode_decision(metric, days, as_of=_MONDAY, open_episode=None)

    assert opened.action is EpisodeAction.OPEN
    assert opened.opened_on == _MONDAY - timedelta(days=7)
    assert not_opened.action is EpisodeAction.NONE


@pytest.mark.parametrize("bucket", ["day", "dow"])
def test_day_and_dow_windows_are_unchanged(bucket: str) -> None:
    metric = replace(_WEEK, bucket=bucket, window_days=60, k_consecutive=3)
    as_of = date(2026, 10, 3)

    first, last = window_bounds(metric, as_of)

    assert last == date(2026, 9, 30)
    assert first == date(2026, 8, 2)  # 60 inclusive days


def test_day_baselines_still_step_one_day_and_dow_buckets_by_weekday() -> None:
    metric = replace(_WEEK, bucket="dow", window_days=14, k_consecutive=1, min_n=1)
    as_of = date(2026, 10, 3)
    samples = {as_of - timedelta(days=d): 10.0 for d in range(1, 15)}

    baselines = compute_baselines(metric, samples, as_of=as_of, now=_NOW)

    assert [b.bucket_value for b in baselines] == [str(i) for i in range(7)]
    assert {b.n_expected for b in baselines} == {2}
    assert bucket_value(metric, date(2026, 10, 5)) == "0"
    assert bucket_value(_WEEK, date(2026, 10, 5)) == "all"
