"""Daily balance + trends baseline math (IEA, tasks.md S9b, bu-jc6htw.2).

Pure, deterministic functions over already-materialized ``daily_rollups``
rows (``chronicler.storage.DailyRollup``) — this module never touches the
database itself. Callers (the ``/balance``/``/trends`` API handlers) fetch
rows via ``storage.list_daily_rollups_range`` and pass the per-lane seconds
into these functions. No I/O, no LLM, no side effects.

"Usual" is a trailing rolling-window mean over ``daily_rollups`` — the
chronicler's own already-materialized per-day/per-lane totals (design.md
"this day vs your usual reads the chronicler's own synthesized baselines").
It deliberately does NOT depend on the separate memory write-back loop
(tasks.md S8, bu-93y4rt) — that loop synthesizes narrative insights for
memory recall, not the numeric baseline this module computes for every
balance/trends request.
"""

from __future__ import annotations

from dataclasses import dataclass

from butlers.chronicler.aggregations import LANES

DEFAULT_BASELINE_LOOKBACK_DAYS = 28
"""Trailing local-day window (excluding the target day) used to compute the
'usual' per-lane baseline for the balance/trends endpoints."""

# A day needs at least this many prior baseline samples before its delta can
# be flagged as an anomaly — guards the start of a rollup history (or a
# recently-added lane) from producing spurious "everything is an anomaly"
# noise when there is nothing real to compare against yet.
ANOMALY_MIN_SAMPLE_DAYS = 7

# A day's delta vs baseline must clear BOTH an absolute floor and a relative
# floor to be flagged — the absolute floor keeps a lane that is usually near-
# zero (e.g. a 5-minute baseline) from flagging on trivial minute-level noise,
# and the relative floor keeps a lane with a large baseline (e.g. 8h work)
# from flagging on a routine few-minutes swing.
ANOMALY_MIN_DELTA_SECONDS = 3600
ANOMALY_MIN_RATIO = 0.5


@dataclass(frozen=True)
class LaneBalance:
    """One lane's totals for one local day, annotated against its baseline."""

    lane: str
    seconds: int
    baseline_seconds: float | None
    """Trailing rolling-window mean seconds for this lane, or ``None`` when
    there is no materialized rollup history yet within the lookback window —
    distinct from a real, computed 0 baseline."""
    delta_seconds: float | None
    """``seconds - baseline_seconds``. Always ``None`` when
    ``baseline_seconds`` is ``None``."""
    baseline_sample_days: int
    """Number of prior materialized days that fed the baseline mean."""


def compute_lane_baseline(
    baseline_seconds_by_lane: dict[str, list[int]],
) -> dict[str, tuple[float | None, int]]:
    """Mean seconds per lane over a trailing window, zero-filled for ``LANES``.

    ``baseline_seconds_by_lane`` maps lane -> list of per-day seconds values
    drawn from materialized ``daily_rollups`` rows (already zero-filled per
    day by ``rollups.compute_daily_lane_rollup``, so every sample is a real
    "0s that day" rather than a missing row). A lane with an empty sample
    list has no baseline yet: returns ``(None, 0)`` rather than a fabricated
    zero — there is a real difference between "usually 0s" and "no history to
    compare against".

    Returns one entry per lane in ``LANES``.
    """
    result: dict[str, tuple[float | None, int]] = {}
    for lane in LANES:
        samples = baseline_seconds_by_lane.get(lane, [])
        if not samples:
            result[lane] = (None, 0)
        else:
            result[lane] = (sum(samples) / len(samples), len(samples))
    return result


def compute_daily_balance(
    day_seconds_by_lane: dict[str, int],
    baseline_seconds_by_lane: dict[str, list[int]],
) -> list[LaneBalance]:
    """Combine one day's per-lane totals with the trailing baseline.

    Returns one ``LaneBalance`` per lane in ``LANES``, sorted by lane name,
    zero-filled for the day the same way ``RollupLaneRow`` is — a lane with
    no activity today still returns ``seconds=0`` alongside its baseline for
    context (per the "Daily Balance Endpoint" spec scenario: "a lane with no
    activity returns zero with its baseline for context").
    """
    baselines = compute_lane_baseline(baseline_seconds_by_lane)
    out: list[LaneBalance] = []
    for lane in sorted(LANES):
        seconds = day_seconds_by_lane.get(lane, 0)
        baseline_seconds, sample_days = baselines[lane]
        delta = None if baseline_seconds is None else seconds - baseline_seconds
        out.append(
            LaneBalance(
                lane=lane,
                seconds=seconds,
                baseline_seconds=baseline_seconds,
                delta_seconds=delta,
                baseline_sample_days=sample_days,
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
    """Whether one day's lane delta vs baseline clears the anomaly thresholds.

    Requires at least ``ANOMALY_MIN_SAMPLE_DAYS`` baseline samples (guards
    against flagging early in a rollup history) and a delta that clears both
    ``ANOMALY_MIN_DELTA_SECONDS`` and ``ANOMALY_MIN_RATIO`` of the baseline —
    see the module docstring for why both floors are required.
    """
    if lane_balance.baseline_seconds is None:
        return False
    if lane_balance.baseline_sample_days < ANOMALY_MIN_SAMPLE_DAYS:
        return False
    if lane_balance.delta_seconds is None:
        return False
    delta = abs(lane_balance.delta_seconds)
    threshold = max(ANOMALY_MIN_DELTA_SECONDS, lane_balance.baseline_seconds * ANOMALY_MIN_RATIO)
    return delta >= threshold


__all__ = [
    "ANOMALY_MIN_DELTA_SECONDS",
    "ANOMALY_MIN_RATIO",
    "ANOMALY_MIN_SAMPLE_DAYS",
    "DEFAULT_BASELINE_LOOKBACK_DAYS",
    "LaneBalance",
    "compute_daily_balance",
    "compute_lane_baseline",
    "compute_lane_streak",
    "is_lane_anomalous",
]
