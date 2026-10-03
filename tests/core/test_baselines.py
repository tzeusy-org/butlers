"""Golden contract for the personal-baseline scorer (synthetic series only)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from butlers.core.baselines import (
    METHOD_VERSION,
    BaselineMetric,
    BaselineState,
    Deviation,
    EpisodeAction,
    OpenEpisode,
    bucket_value,
    compute_baselines,
    episode_decision,
    episode_excluded_days,
    score,
    validate_baseline_evidence,
    window_bounds,
)

pytestmark = pytest.mark.unit

_AS_OF = date(2026, 10, 3)
_NOW = datetime(2026, 10, 4, 6, tzinfo=UTC)

_HR = BaselineMetric(
    key="resting_hr",
    unit="bpm",
    bucket="day",
    window_days=60,
    min_n=14,
    min_coverage=0.5,
    k_mad=2.6,
    k_consecutive=3,
    concern="above",
    min_dispersion=1.0,
    expected_signal_key="health:baseline:resting_hr",
)


def _series(metric: BaselineMetric, values: dict[int, float]) -> dict[date, float]:
    """Samples keyed by offset (days before the window end); window ends before the run."""
    _, last = window_bounds(metric, _AS_OF)
    return {last - timedelta(days=offset): value for offset, value in values.items()}


def _usual(metric: BaselineMetric, *, missing: set[int] = frozenset()) -> dict[date, float]:
    """54 of 60 days observed, alternating 54 / 56 / 58 around a center of 56."""
    cycle = (54.0, 56.0, 58.0)
    return _series(
        metric,
        {i: cycle[i % 3] for i in range(metric.window_days) if i not in missing},
    )


def _ready(metric: BaselineMetric = _HR, **kwargs):
    samples = _usual(metric, missing={2, 9, 17, 30, 41, 55})
    (baseline,) = compute_baselines(metric, samples, as_of=_AS_OF, now=_NOW, **kwargs)
    return baseline


def test_window_ends_before_the_scored_run() -> None:
    first, last = window_bounds(_HR, _AS_OF)
    assert last == _AS_OF - timedelta(days=3)
    assert (last - first).days == 59


def test_band_carries_its_honest_denominator() -> None:
    baseline = _ready()

    assert baseline.status == "ready"
    assert (baseline.n_observed, baseline.n_expected) == (54, 60)
    assert baseline.coverage == pytest.approx(0.9)
    assert baseline.center == 56.0
    assert baseline.method_version == METHOD_VERSION
    low, high = baseline.band
    assert low < 56.0 < high
    assert baseline.evidence()["n_observed"] == 54


def test_coverage_gap_is_insufficient_history_never_within() -> None:
    sparse = _series(_HR, {i: 56.0 for i in range(0, 60, 6)})  # 10 of 60
    (baseline,) = compute_baselines(_HR, sparse, as_of=_AS_OF, now=_NOW)

    assert baseline.status == "insufficient_history"
    assert baseline.center is None and baseline.band is None
    deviation = score(_HR, 56.0, baseline, now=_NOW)
    assert deviation.state is BaselineState.INSUFFICIENT_HISTORY
    assert not deviation.concerning


def test_low_coverage_with_enough_points_is_still_insufficient() -> None:
    metric = replace(_HR, min_n=5, min_coverage=0.8)
    samples = _series(metric, {i: 56.0 for i in range(0, 60, 2)})  # 30 of 60 = 0.5
    (baseline,) = compute_baselines(metric, samples, as_of=_AS_OF, now=_NOW)

    assert baseline.n_observed == 30
    assert baseline.status == "insufficient_history"


def test_flat_series_is_floored_at_min_dispersion() -> None:
    flat = _series(_HR, {i: 56.0 for i in range(60)})
    (baseline,) = compute_baselines(_HR, flat, as_of=_AS_OF, now=_NOW)

    assert baseline.dispersion == _HR.min_dispersion
    # 56 + 2.6 * 1.0 is the edge: just inside is within, just outside is above.
    assert score(_HR, 58.5, baseline, now=_NOW).state is BaselineState.WITHIN
    assert score(_HR, 58.7, baseline, now=_NOW).state is BaselineState.ABOVE


def test_unmeasurable_instrument_wins_over_history() -> None:
    (baseline,) = compute_baselines(_HR, _usual(_HR), as_of=_AS_OF, now=_NOW, measurable=False)

    assert baseline.status == "unmeasurable" and baseline.center is None
    assert score(_HR, 99.0, baseline, now=_NOW).state is BaselineState.UNMEASURABLE


def test_stale_band_never_scores() -> None:
    baseline = _ready()
    later = _NOW + 2 * _HR.refresh_interval + timedelta(seconds=1)

    assert score(_HR, 99.0, baseline, now=later).state is BaselineState.STALE
    assert score(_HR, 99.0, baseline, now=_NOW).state is BaselineState.ABOVE


def test_concern_direction_filters_but_state_stays_honest() -> None:
    baseline = _ready()

    low = score(_HR, 40.0, baseline, now=_NOW)
    assert low.state is BaselineState.BELOW and not low.concerning
    high = score(_HR, 70.0, baseline, now=_NOW)
    assert high.state is BaselineState.ABOVE and high.concerning

    both = replace(_HR, concern="both")
    assert score(both, 40.0, baseline, now=_NOW).concerning


def test_episode_days_are_excluded_from_numerator_and_denominator() -> None:
    _, last = window_bounds(_HR, _AS_OF)
    contaminated = _usual(_HR)
    episode_days = {last - timedelta(days=i) for i in range(10, 15)}
    for day in episode_days:
        contaminated[day] = 90.0

    (without,) = compute_baselines(_HR, contaminated, as_of=_AS_OF, now=_NOW)
    (excluded,) = compute_baselines(
        _HR, contaminated, as_of=_AS_OF, now=_NOW, excluded_days=episode_days
    )

    assert excluded.n_expected == 55 and excluded.n_observed == 55
    assert excluded.center == 56.0
    assert without.n_expected == 60
    assert excluded.input_digest != without.input_digest


def test_episode_excluded_days_span_is_half_open_for_closed_episodes() -> None:
    open_span = (date(2026, 9, 28), None)
    closed_span = (date(2026, 9, 1), date(2026, 9, 4))

    excluded = episode_excluded_days([open_span, closed_span], as_of=_AS_OF)

    assert date(2026, 9, 3) in excluded and date(2026, 9, 4) not in excluded
    assert date(2026, 10, 3) in excluded and date(2026, 9, 27) not in excluded


def test_dow_buckets_use_the_local_calendar_weekday() -> None:
    metric = replace(_HR, bucket="dow", min_n=4, min_coverage=0.5)
    monday, tuesday = date(2026, 9, 28), date(2026, 9, 29)
    assert bucket_value(metric, monday) == "0" and bucket_value(metric, tuesday) == "1"
    assert bucket_value(_HR, monday) == "all"

    # Mondays run high (70), every other day 56: the Monday band must not blend them.
    _, last = window_bounds(metric, _AS_OF)
    samples = {
        last - timedelta(days=i): 70.0 if (last - timedelta(days=i)).weekday() == 0 else 56.0
        for i in range(60)
    }
    bands = {b.bucket_value: b for b in compute_baselines(metric, samples, as_of=_AS_OF, now=_NOW)}

    assert set(bands) == {str(n) for n in range(7)}
    assert bands["0"].center == 70.0 and bands["1"].center == 56.0
    assert score(metric, 70.0, bands["0"], now=_NOW).state is BaselineState.WITHIN
    assert score(metric, 70.0, bands["1"], now=_NOW).state is BaselineState.ABOVE


def test_unchanged_inputs_yield_an_identical_digest() -> None:
    assert _ready().input_digest == _ready().input_digest
    other = compute_baselines(_HR, _usual(_HR), as_of=_AS_OF - timedelta(days=1), now=_NOW)
    assert other[0].input_digest != _ready().input_digest


# ---------------------------------------------------------------------------
# Episode decisions
# ---------------------------------------------------------------------------


def _dev(state: BaselineState, mad: float | None, *, concerning: bool) -> Deviation:
    return Deviation(state, mad, 54, 60, 0.9, concerning)


_HIGH = _dev(BaselineState.ABOVE, 3.4, concerning=True)
_OK = _dev(BaselineState.WITHIN, 0.2, concerning=False)


def _run(*items: Deviation | None) -> dict[date, Deviation | None]:
    return {_AS_OF - timedelta(days=len(items) - 1 - i): item for i, item in enumerate(items)}


def test_three_consecutive_concerning_days_open_one_episode() -> None:
    decision = episode_decision(_HR, _run(_HIGH, _HIGH, _HIGH), as_of=_AS_OF, open_episode=None)

    assert decision.action is EpisodeAction.OPEN
    assert decision.opened_on == _AS_OF - timedelta(days=2)
    assert decision.direction == "above" and decision.peak_mad == pytest.approx(3.4)


@pytest.mark.parametrize(
    "run",
    [
        (_HIGH, _OK, _HIGH),
        (None, _HIGH, _HIGH),
        (_HIGH, _HIGH, _dev(BaselineState.STALE, None, concerning=False)),
        (_HIGH, _HIGH, _dev(BaselineState.BELOW, -3.0, concerning=False)),
        (_HIGH, _dev(BaselineState.BELOW, -3.0, concerning=True), _HIGH),
    ],
)
def test_broken_or_unscoreable_runs_open_nothing(run) -> None:
    decision = episode_decision(_HR, _run(*run), as_of=_AS_OF, open_episode=None)

    assert decision.action is EpisodeAction.NONE


def test_return_to_band_closes_and_silence_does_not() -> None:
    episode = OpenEpisode("e1", _AS_OF - timedelta(days=4), "above", 3.1)

    closed = episode_decision(_HR, _run(_HIGH, _OK), as_of=_AS_OF, open_episode=episode)
    held_missing = episode_decision(_HR, _run(_HIGH, None), as_of=_AS_OF, open_episode=episode)
    held_dead = episode_decision(
        _HR,
        _run(_HIGH, _dev(BaselineState.UNMEASURABLE, None, concerning=False)),
        as_of=_AS_OF,
        open_episode=episode,
    )
    still = episode_decision(
        _HR,
        _run(_HIGH, _dev(BaselineState.ABOVE, 4.0, concerning=True)),
        as_of=_AS_OF,
        open_episode=episode,
    )

    assert closed.action is EpisodeAction.CLOSE
    assert held_missing.action is EpisodeAction.HOLD
    assert held_dead.action is EpisodeAction.HOLD
    assert still.action is EpisodeAction.CONTINUE and still.peak_mad == 4.0


# ---------------------------------------------------------------------------
# baseline_evidence gate
# ---------------------------------------------------------------------------


def test_evidence_from_a_baseline_validates() -> None:
    assert validate_baseline_evidence(_ready().evidence()) is None


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {},
        {"n_observed": 54, "n_expected": 60, "coverage": 0.9, "center": 56.0},
        {
            "n_observed": 61,
            "n_expected": 60,
            "coverage": 1.0,
            "center": 1,
            "dispersion": 1,
            "method_version": "mad-v1",
        },
        {
            "n_observed": 54,
            "n_expected": 60,
            "coverage": 0.9,
            "center": 56.0,
            "dispersion": 1.0,
            "method_version": "",
        },
        {
            "n_observed": True,
            "n_expected": 60,
            "coverage": 0.9,
            "center": 56.0,
            "dispersion": 1.0,
            "method_version": "mad-v1",
        },
    ],
)
def test_incomplete_evidence_is_rejected(evidence) -> None:
    assert validate_baseline_evidence(evidence) is not None
