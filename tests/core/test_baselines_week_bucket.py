"""The ``week`` bucket of the personal-baseline scorer (bu-q7vx1q.94.2).

A week sample is keyed by the date its week starts on; the caller picks the anchor weekday.
The ``day`` and ``dow`` buckets must not move.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from butlers.core.baselines import (
    METHOD_VERSION,
    Baseline,
    BaselineMetric,
    BaselineState,
    Deviation,
    EpisodeAction,
    compute_baselines,
    episode_decision,
    score,
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


def test_a_week_window_is_weekly_anchors_and_expects_one_sample_per_week() -> None:
    first, last = window_bounds(_WEEK, _MONDAY)

    assert last == _MONDAY - timedelta(days=7)
    assert first == last - timedelta(days=49)
    assert (last - first).days // 7 + 1 == 8
    with pytest.raises(ValueError, match="divisible by 7"):
        replace(_WEEK, window_days=60)

    (empty,) = compute_baselines(_WEEK, {}, as_of=_MONDAY, now=_NOW)
    assert (empty.bucket_value, empty.n_expected, empty.status) == (
        "all",
        8,
        "insufficient_history",
    )

    samples = _weekly([20, 22, 21, 19, 23, 21])
    (ready,) = compute_baselines(_WEEK, samples, as_of=_MONDAY, now=_NOW)
    assert (ready.status, ready.n_observed, ready.n_expected, ready.center) == ("ready", 6, 8, 21)
    assert ready.band is not None and ready.band[0] < 21 < ready.band[1]

    (thin,) = compute_baselines(_WEEK, _weekly([20, 22, 21]), as_of=_MONDAY, now=_NOW)
    assert (thin.status, thin.center, thin.n_observed) == ("insufficient_history", None, 3)

    off_anchor = {_MONDAY - timedelta(days=7 * i + 3): 21.0 for i in range(1, 7)}
    (daily,) = compute_baselines(_WEEK, off_anchor, as_of=_MONDAY, now=_NOW)
    assert daily.n_observed == 0

    (episode,) = compute_baselines(
        _WEEK,
        samples,
        as_of=_MONDAY,
        now=_NOW,
        excluded_days={_MONDAY - timedelta(days=14)},
    )
    assert episode.n_expected == 7


def test_an_episode_opens_on_consecutive_weeks_not_days_and_day_windows_hold() -> None:
    metric = replace(_WEEK, k_consecutive=2)
    above = Deviation(BaselineState.ABOVE, 4.0, 6, 8, 0.75, True)
    weeks = {_MONDAY: above, _MONDAY - timedelta(days=7): above}
    days = {_MONDAY: above, _MONDAY - timedelta(days=1): above}

    opened = episode_decision(metric, weeks, as_of=_MONDAY, open_episode=None)
    not_opened = episode_decision(metric, days, as_of=_MONDAY, open_episode=None)

    assert opened.action is EpisodeAction.OPEN
    assert opened.opened_on == _MONDAY - timedelta(days=7)
    assert not_opened.action is EpisodeAction.NONE

    for bucket in ("day", "dow"):
        unchanged = replace(_WEEK, bucket=bucket, window_days=60, k_consecutive=3)
        first, last = window_bounds(unchanged, date(2026, 10, 3))
        assert last == date(2026, 9, 30)
        assert first == date(2026, 8, 2)  # 60 inclusive days


def _ready_baseline() -> Baseline:
    """A band with centre 20 and dispersion 2, so k_mad = 2 puts the edges at exactly 16 and 24."""
    return Baseline(
        metric_key="edge",
        bucket_value="all",
        method_version=METHOD_VERSION,
        status="ready",
        measurability="measurable",
        unit="kWh",
        center=20.0,
        dispersion=2.0,
        n_observed=6,
        n_expected=8,
        coverage=0.75,
        k_mad=2.0,
        input_digest="d",
        computed_at=_NOW,
    )


def test_a_value_exactly_k_mad_from_the_centre_is_outside_the_band() -> None:
    metric = replace(_WEEK, k_mad=2.0, concern="both")
    baseline = _ready_baseline()

    above = score(metric, 24.0, baseline, now=_NOW)
    below = score(metric, 16.0, baseline, now=_NOW)

    assert (above.mad_units, above.state) == (2.0, BaselineState.ABOVE)
    assert (below.mad_units, below.state) == (-2.0, BaselineState.BELOW)
    assert score(metric, 23.5, baseline, now=_NOW).state is BaselineState.WITHIN
    assert score(metric, 16.5, baseline, now=_NOW).state is BaselineState.WITHIN
