"""Daily balance + trends baseline math (IEA, tasks.md S9b, bu-jc6htw.2).

Pure, deterministic functions over already-materialized ``daily_rollups``
rows (``chronicler.storage.DailyRollup``) — this module never touches the
database itself. Callers (the ``/balance``/``/trends`` API handlers) fetch
rows via ``storage.list_daily_rollups_range`` and pass the per-lane, per-day
seconds into these functions. No I/O, no LLM, no side effects.

"Usual" is the owner's personal band from :mod:`butlers.core.baselines` — the
median and a MAD-derived dispersion of the lane's per-day seconds over the
trailing ``lookback_days`` local days before the scored day (bu-q7vx1q.80, so
one scorer is behind every "unusual for you" claim). The median is not dragged
by the very spike being judged, ``n_observed`` of ``n_expected`` days are
carried honestly, and a lane without enough history reads
``insufficient_history`` with no center, never a fabricated "within". The band is
computed per request from the chronicler's own materialized per-day/per-lane
totals; nothing is persisted.

It deliberately does NOT depend on the separate memory write-back loop
(tasks.md S8, bu-93y4rt) — that loop synthesizes narrative insights for
memory recall, not the numeric baseline this module computes for every
balance/trends request.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from butlers.chronicler.aggregations import LANES
from butlers.core.baselines import (
    Baseline,
    BaselineMetric,
    BaselineState,
    compute_baselines,
    score,
)

DEFAULT_BASELINE_LOOKBACK_DAYS = 28
"""Trailing local-day window (excluding the target day) used to compute the
'usual' per-lane baseline for the balance/trends endpoints."""

LANE_BASELINE_MIN_N = 7
"""Prior materialized days a lane needs before it can be scored at all — guards
the start of a rollup history (or a recently-added lane) from producing
spurious "everything is an anomaly" noise when there is nothing real to compare
against yet. Capped at the lookback window for short ``lookback_days``."""

LANE_BASELINE_MIN_COVERAGE = 0.25
"""Share of the lookback window that must have a materialized rollup; missing
days lower coverage and a thin window reads ``insufficient_history``."""

LANE_BASELINE_K_MAD = 3.0
"""Band half-width in MAD-derived dispersions: a day further than this from the
median is above/below."""

LANE_MIN_DISPERSION_SECONDS = 3600.0
"""Dispersion floor. A flat or near-zero lane would otherwise have a zero-width
band and flag minute-level noise; the floor keeps the band at least
``LANE_BASELINE_K_MAD`` hours wide on each side."""


def lane_metric(lane: str, lookback_days: int) -> BaselineMetric:
    """The declared personal-baseline metric for one chronicler lane."""
    return BaselineMetric(
        key=f"chronicler.lane.{lane}",
        unit="seconds",
        bucket="day",
        window_days=lookback_days,
        min_n=min(LANE_BASELINE_MIN_N, lookback_days),
        min_coverage=LANE_BASELINE_MIN_COVERAGE,
        k_mad=LANE_BASELINE_K_MAD,
        # The scored day is judged on its own: the window is the lookback days
        # immediately before it.
        k_consecutive=1,
        concern="both",
        min_dispersion=LANE_MIN_DISPERSION_SECONDS,
        # Chronicles has no expected-signal instrument here; coverage carries
        # the honesty, so every lane is passed as measurable.
        expected_signal_key=f"chronicler.daily_rollups.{lane}",
    )


def _scoring_instant(local_date: date) -> datetime:
    """A deterministic 'now' for one scored day (the scorer takes it as input)."""
    return datetime.combine(local_date, time.min, tzinfo=UTC)


@dataclass(frozen=True)
class LaneBalance:
    """One lane's totals for one local day, annotated against its baseline."""

    lane: str
    seconds: int
    baseline_seconds: float | None
    """The band center (median seconds) for this lane, or ``None`` when the
    lane has too little materialized rollup history in the lookback window —
    distinct from a real, computed 0 baseline."""
    delta_seconds: float | None
    """``seconds - baseline_seconds``. Always ``None`` when
    ``baseline_seconds`` is ``None``."""
    baseline_sample_days: int
    """Number of prior materialized days that fed the band (``n_observed``)."""
    state: str = BaselineState.INSUFFICIENT_HISTORY.value
    """``within`` / ``above`` / ``below`` / ``insufficient_history`` — what the
    day can honestly be called against the band."""
    mad_units: float | None = None
    """Signed distance from the center in dispersions, when scored."""
    band_low: float | None = None
    band_high: float | None = None
    """The usual range in seconds, when the band is ready."""


def compute_lane_baseline(
    baseline_samples_by_lane: Mapping[str, Mapping[date, int]],
    *,
    local_date: date,
    lookback_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
) -> dict[str, Baseline]:
    """The personal-baseline band per lane for the day ``local_date``.

    ``baseline_samples_by_lane`` maps lane -> {local day -> seconds} drawn from
    materialized ``daily_rollups`` rows (already zero-filled per lane by
    ``rollups.compute_daily_lane_rollup``, so every sample is a real "0s that
    day"). Days outside the ``lookback_days`` before ``local_date`` are ignored;
    days with no materialized rollup are missing samples and lower coverage.
    A lane with too few samples is ``insufficient_history`` with no center —
    there is a real difference between "usually 0s" and "no history".

    Returns one entry per lane in ``LANES``.
    """
    result: dict[str, Baseline] = {}
    for lane in LANES:
        samples = {day: float(sec) for day, sec in baseline_samples_by_lane.get(lane, {}).items()}
        (result[lane],) = compute_baselines(
            lane_metric(lane, lookback_days),
            samples,
            as_of=local_date,
            now=_scoring_instant(local_date),
        )
    return result


def compute_daily_balance(
    day_seconds_by_lane: dict[str, int],
    baseline_samples_by_lane: Mapping[str, Mapping[date, int]],
    *,
    local_date: date,
    lookback_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
) -> list[LaneBalance]:
    """Combine one day's per-lane totals with its personal-baseline band.

    Returns one ``LaneBalance`` per lane in ``LANES``, sorted by lane name,
    zero-filled for the day the same way ``RollupLaneRow`` is — a lane with
    no activity today still returns ``seconds=0`` alongside its baseline for
    context (per the "Daily Balance Endpoint" spec scenario: "a lane with no
    activity returns zero with its baseline for context").
    """
    baselines = compute_lane_baseline(
        baseline_samples_by_lane, local_date=local_date, lookback_days=lookback_days
    )
    now = _scoring_instant(local_date)
    out: list[LaneBalance] = []
    for lane in sorted(LANES):
        seconds = day_seconds_by_lane.get(lane, 0)
        baseline = baselines[lane]
        deviation = score(lane_metric(lane, lookback_days), float(seconds), baseline, now=now)
        band = baseline.band
        out.append(
            LaneBalance(
                lane=lane,
                seconds=seconds,
                baseline_seconds=baseline.center,
                delta_seconds=None if baseline.center is None else seconds - baseline.center,
                baseline_sample_days=baseline.n_observed,
                state=deviation.state.value,
                mad_units=deviation.mad_units,
                band_low=band[0] if band else None,
                band_high=band[1] if band else None,
            )
        )
    return out


def compute_lane_streak(daily_seconds: list[int]) -> int:
    """Count the trailing run of consecutive non-zero days.

    ``daily_seconds`` is ordered oldest -> newest (one entry per day in the
    requested window). Returns 0 immediately if the most recent day is zero
    or the list is empty — this is a *current* streak, not a historical max.
    """
    streak = 0
    for seconds in reversed(daily_seconds):
        if seconds <= 0:
            break
        streak += 1
    return streak


def is_lane_anomalous(lane_balance: LaneBalance) -> bool:
    """Whether one day's lane total is outside its personal band.

    True only for a scored ``above``/``below`` day. A lane without enough
    history (``insufficient_history``) is never an anomaly, so the start of a
    rollup history cannot produce a fabricated one.
    """
    return lane_balance.state in (BaselineState.ABOVE.value, BaselineState.BELOW.value)


__all__ = [
    "DEFAULT_BASELINE_LOOKBACK_DAYS",
    "LANE_BASELINE_K_MAD",
    "LANE_BASELINE_MIN_COVERAGE",
    "LANE_BASELINE_MIN_N",
    "LANE_MIN_DISPERSION_SECONDS",
    "LaneBalance",
    "compute_daily_balance",
    "compute_lane_baseline",
    "compute_lane_streak",
    "is_lane_anomalous",
    "lane_metric",
]
