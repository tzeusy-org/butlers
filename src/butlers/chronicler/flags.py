"""Deterministic anomaly-flag rules on daily rollups (bu-v76a7, telemetry-
distillation bead 4, design doc §2.5/§3.4 + openspec change
``chronicler-telemetry-distillation`` spec.md "Deterministic Anomaly Flags").

Four flags, one row per ``(local_date, flag_type)``, written to
``chronicler.daily_rollup_flags`` (migration ``chronicler_019``, bead 3):

- ``feeder_dark`` — a source is a known outage: ``source_adapter_state.active
  = false``, or its checkpoint watermark is stale beyond
  :data:`FEEDER_STALE_MULTIPLE` times its scheduled cron interval
  (:data:`SOURCE_CRON_MINUTES`). Evaluated *first* and gates every behavioral
  flag below — design doc §2.5 "classify before flagging": a feeder outage is
  a data problem, never a fabricated all-clear zero, and never allowed to
  masquerade as a genuine behavioral anomaly.
- ``sleep_missing`` — the day's ``sleep`` lane rolled up to zero seconds,
  *and* the source that writes sleep episodes
  (``google_health.measurements``) is healthy.
- ``routine_break`` — an enabled routine's window (matching weekday) has no
  corroborated ``occupation_block`` episode, *and* the sources that would
  have produced one are healthy.
- ``lane_share_outlier`` — a lane's share of the day's total tracked seconds
  deviates sharply from its trailing-14-day median share, the day clears a
  minimum-evidence floor, *and* every source that can contribute to that
  lane is healthy.

Structurally mirrors ``rollups.py``'s split:

- ``compute_*`` functions — pure, dependency-light, exercised directly by
  unit tests.
- :func:`evaluate_and_write_daily_flags` — the async orchestrator. Reads
  whatever DB state each rule needs and reconciles
  ``chronicler.daily_rollup_flags`` to exactly what the rules say should
  exist for one local day: upserts a row for every rule that holds, deletes
  the row for every managed ``flag_type`` that does not (so a late
  correction that fixes a previously-flagged condition clears the stale flag
  on the next run instead of leaving a dangling row — the "idempotent
  re-runs" requirement covers removal, not just non-duplication).

Must run *after* ``rollups.materialize_daily_rollups`` has written the day's
``daily_rollups`` rows (bead 3) — this module reads them, it does not
recompute lane totals itself. Wired as a chained step inside
``jobs.run_rollup_daily`` (see that module), not a separate scheduled job.

No LLM call anywhere (RFC 0014 §D5) — every rule here is a pure function
over already-materialized rows plus ``source_adapter_state``/
``projection_checkpoints``.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from butlers.chronicler.aggregations import LANES, sources_for_lane
from butlers.chronicler.models import ProjectionCheckpoint, SourceAdapterState
from butlers.chronicler.storage import (
    delete_daily_rollup_flag,
    get_checkpoint,
    get_source_state,
    list_daily_rollups,
    list_episodes,
    list_routines,
    upsert_daily_rollup_flag,
)

FLAG_FEEDER_DARK = "feeder_dark"
FLAG_SLEEP_MISSING = "sleep_missing"
FLAG_ROUTINE_BREAK = "routine_break"
FLAG_LANE_SHARE_OUTLIER = "lane_share_outlier"

# The four rule flag_types this module manages. Used to reconcile exactly
# these rows on every run (see module docstring) — a flag_type outside this
# set (e.g. a future rule, or a manually-inserted row) is left untouched.
MANAGED_FLAG_TYPES: tuple[str, ...] = (
    FLAG_FEEDER_DARK,
    FLAG_SLEEP_MISSING,
    FLAG_ROUTINE_BREAK,
    FLAG_LANE_SHARE_OUTLIER,
)

# ── feeder_dark ──────────────────────────────────────────────────────────

# A source counts as dark once its checkpoint watermark is this many times
# its scheduled cron interval old. Given explicitly by the design doc ("2x
# its cron interval"), not a [decision].
FEEDER_STALE_MULTIPLE = 2

# [decision] No structured per-source cron-interval registry exists yet
# (`source_adapter_state`/`projection_checkpoints` carry no schedule
# metadata). Building one is out of scope for a Size S bead — this mirrors
# `roster/chronicler/butler.toml` + the module-default schedules in
# `roster/chronicler/modules/__init__.py` directly, as of 2026-07-06.
# Scoped to exactly the sources that gate one of this bead's four flags
# (every source `aggregations.sources_for_lane` can return for some lane,
# plus the two direct `routine_break` dependencies) — evaluating staleness
# for a source no flag here depends on would mean guessing an interval with
# no test coverage to validate it. Update alongside butler.toml if a listed
# job's cadence changes.
SOURCE_CRON_MINUTES: dict[str, int] = {
    "core.sessions": 15,
    "spotify.session_summary": 30,
    "steam.play_history": 30,
    "owntracks.points": 30,
    "owntracks.place_cluster": 30,
    "google_health.measurements": 30,
    "health.meals": 30,
    "home_assistant.history": 30,
    "chronicler.focus_inferred": 30,
    "chronicler.reading_inferred": 30,
    "chronicler.exercise_inferred": 30,
    "comms.message_bursts": 15,
    "activitywatch.window": 15,
    "chronicler.occupation_inferred": 60,
    "home_assistant.sensor_activity": 30,
}

# Direct source dependency for sleep_missing (writes sleep_episode).
_SLEEP_SOURCE = "google_health.measurements"

# Direct source dependencies for routine_break: the adapter that would write
# occupation_block, plus its best-established corroborator. [decision]
# `owner_outbound.messages` is deliberately excluded — per
# `adapters/occupation.py`'s own docstring it is still landing, and querying
# it before deployment already degrades to zero matches by design, not an
# outage signal.
_ROUTINE_BREAK_SOURCES: frozenset[str] = frozenset(
    {"chronicler.occupation_inferred", "spotify.session_summary"}
)

_OCCUPATION_SOURCE_NAME = "chronicler.occupation_inferred"
_OCCUPATION_EPISODE_TYPE = "occupation_block"

# ── lane_share_outlier ───────────────────────────────────────────────────

# Given explicitly by the design doc ("trailing-14-day median").
LANE_SHARE_TRAILING_DAYS = 14

# Given explicitly by the design doc (">2x ... median"). Applied as a
# symmetric ratio in either direction (today's share at least this many
# times the median, or at most 1/this-many) — a defensible reading of "2x
# deviation" absent a more precise spec. [decision]
LANE_SHARE_OUTLIER_MULTIPLE = 2.0

# [decision] Minimum number of trailing days with any tracked activity
# before a lane's median share is considered meaningful. Below this, a
# freshly-onboarded system (or a lane just starting to accrue history) has
# too little history for "2x the median" to mean anything — skip rather
# than flag on noise.
MIN_TRAILING_DAYS_FOR_MEDIAN = 5

# [decision] Total tracked activity-layer seconds across all lanes for the
# day must clear this floor before *any* lane_share_outlier check runs for
# that day — guards the design's named failure mode ("a low-evidence day
# producing a spurious 100%-in-one-lane outlier"). One hour is a low bar
# deliberately: only days with almost no tracked evidence at all are
# excluded.
MIN_EVIDENCE_FLOOR_SECONDS = 3600


def _now() -> datetime:
    """Wall-clock now, isolated for test patching."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Pure rule functions
# ---------------------------------------------------------------------------


def is_source_dark(
    state: SourceAdapterState | None,
    checkpoint: ProjectionCheckpoint | None,
    *,
    cron_minutes: int,
    now: datetime,
) -> bool:
    """Whether a source counts as a feeder outage right now.

    A source is dark when either is true:

    - it has no registration or ``active`` is ``False`` (covers both an
      explicit outage and a source that has never completed a first
      successful run — ``SourceAdapterState.active`` defaults ``False``
      until an adapter calls ``mark_source_active(active=True)``);
    - its checkpoint watermark (``last_success_at``, falling back to
      ``last_run_at`` if unset) is older than
      ``cron_minutes * FEEDER_STALE_MULTIPLE`` minutes — the job has
      silently stopped running/succeeding without anyone flipping
      ``active`` to ``False``.

    A source with ``active=True`` but no checkpoint timestamps at all is
    *not* considered dark by staleness alone — there is nothing to measure
    staleness against, so the ``active`` flag is trusted on its own.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if state is None or not state.active:
        return True
    watermark = None
    if checkpoint is not None:
        watermark = checkpoint.last_success_at or checkpoint.last_run_at
    if watermark is None:
        return False
    stale_after = timedelta(minutes=cron_minutes * FEEDER_STALE_MULTIPLE)
    return (now - watermark) > stale_after


def compute_sleep_missing(sleep_seconds: int, *, sleep_source_dark: bool) -> bool:
    """Whether ``sleep_missing`` should fire for the day.

    ``False`` unconditionally when the sleep feeder is dark — the absence of
    sleep data on an outage day is explained by the outage, not a genuine
    "no sleep recorded" behavioral fact (classify-before-flagging).

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if sleep_source_dark:
        return False
    return sleep_seconds <= 0


def compute_routine_breaks(
    candidates: Sequence[Mapping[str, Any]],
    *,
    routine_source_dark: bool,
) -> list[dict[str, Any]]:
    """Filter routine candidates down to genuine breaks.

    ``candidates`` is one entry per enabled routine whose window matches the
    day's weekday, each carrying ``routine_id``, ``label``, and
    ``has_occupation_block`` (whether a corroborated ``occupation_block``
    episode already covers the window). Returns the candidates that lack a
    block — unless the sources that would have produced one are dark, in
    which case no break is reported at all (an outage explains the absence,
    it is not evidence the routine was actually skipped).

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if routine_source_dark:
        return []
    return [dict(c) for c in candidates if not c["has_occupation_block"]]


def compute_lane_share_outliers(
    today_seconds_by_lane: Mapping[str, float],
    trailing_seconds_by_lane_by_date: Mapping[date, Mapping[str, float]],
    *,
    dark_lanes: Sequence[str] = (),
    min_evidence_seconds: float = MIN_EVIDENCE_FLOOR_SECONDS,
    min_trailing_days: int = MIN_TRAILING_DAYS_FOR_MEDIAN,
    outlier_multiple: float = LANE_SHARE_OUTLIER_MULTIPLE,
) -> dict[str, dict[str, float]]:
    """Return every lane whose share of the day deviates from its trailing
    median beyond ``outlier_multiple``, keyed by lane.

    Guards, in order:

    - the day's total tracked seconds (summed across every lane) must clear
      ``min_evidence_seconds``, or no lane is evaluated at all (a
      low-evidence day cannot produce a meaningful share);
    - a lane in ``dark_lanes`` (a contributing source is a known feeder
      outage) is never evaluated — classify-before-flagging;
    - a lane needs at least ``min_trailing_days`` of trailing history with
      nonzero total tracked seconds before its median share means anything;
      otherwise it is skipped.

    Each returned entry carries ``{"share_today": float, "median_share":
    float}`` for the flag's ``detail`` payload.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    total_today = sum(today_seconds_by_lane.values())
    if total_today < min_evidence_seconds:
        return {}

    dark_lane_set = set(dark_lanes)
    outliers: dict[str, dict[str, float]] = {}

    for lane in LANES:
        if lane in dark_lane_set:
            continue

        share_today = today_seconds_by_lane.get(lane, 0.0) / total_today

        trailing_shares: list[float] = []
        for day_totals in trailing_seconds_by_lane_by_date.values():
            day_total = sum(day_totals.values())
            if day_total <= 0:
                continue
            trailing_shares.append(day_totals.get(lane, 0.0) / day_total)

        if len(trailing_shares) < min_trailing_days:
            continue

        median_share = statistics.median(trailing_shares)

        if median_share <= 0:
            if share_today > 0:
                outliers[lane] = {"share_today": share_today, "median_share": median_share}
            continue

        ratio = share_today / median_share
        if ratio >= outlier_multiple or ratio <= 1.0 / outlier_multiple:
            outliers[lane] = {"share_today": share_today, "median_share": median_share}

    return outliers


# ---------------------------------------------------------------------------
# Async orchestrator
# ---------------------------------------------------------------------------


async def _occupation_block_covers_window(
    pool: asyncpg.Pool,
    *,
    start_at: datetime,
    end_at: datetime,
) -> bool:
    rows = await list_episodes(
        pool,
        source_name=_OCCUPATION_SOURCE_NAME,
        episode_type=_OCCUPATION_EPISODE_TYPE,
        overlaps_with=(start_at, end_at),
        limit=1,
    )
    return bool(rows)


async def _dark_sources(
    pool: asyncpg.Pool,
    *,
    now: datetime,
) -> set[str]:
    dark: set[str] = set()
    for source_name, cron_minutes in SOURCE_CRON_MINUTES.items():
        state = await get_source_state(pool, source_name)
        checkpoint = await get_checkpoint(pool, source_name)
        if is_source_dark(state, checkpoint, cron_minutes=cron_minutes, now=now):
            dark.add(source_name)
    return dark


async def _routine_break_candidates(
    pool: asyncpg.Pool,
    *,
    local_date: date,
    default_timezone: str,
) -> list[dict[str, Any]]:
    routines = await list_routines(pool, enabled_only=True)
    if not routines:
        return []

    weekday_bit = 1 << local_date.weekday()
    candidates: list[dict[str, Any]] = []
    for routine in routines:
        if not (routine.dow_mask & weekday_bit):
            continue
        try:
            tzinfo = ZoneInfo(routine.timezone or default_timezone)
        except ZoneInfoNotFoundError:
            tzinfo = ZoneInfo(default_timezone)

        start_at = datetime.combine(local_date, routine.window_start_local, tzinfo).astimezone(UTC)
        end_at = datetime.combine(local_date, routine.window_end_local, tzinfo).astimezone(UTC)
        has_block = await _occupation_block_covers_window(pool, start_at=start_at, end_at=end_at)
        candidates.append(
            {
                "routine_id": str(routine.id),
                "label": routine.label,
                "has_occupation_block": has_block,
            }
        )
    return candidates


async def _reconcile_flag(
    pool: asyncpg.Pool,
    *,
    local_date: date,
    flag_type: str,
    should_exist: bool,
    severity: str,
    detail: dict[str, Any],
) -> None:
    if should_exist:
        await upsert_daily_rollup_flag(
            pool, local_date=local_date, flag_type=flag_type, severity=severity, detail=detail
        )
    else:
        await delete_daily_rollup_flag(pool, local_date=local_date, flag_type=flag_type)


async def evaluate_and_write_daily_flags(
    pool: asyncpg.Pool,
    *,
    local_date: date,
    timezone: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate all four anomaly-flag rules for one already-materialized
    local day and reconcile ``chronicler.daily_rollup_flags`` to match.

    Must run after ``rollups.materialize_daily_rollups`` has written
    ``local_date``'s ``daily_rollups`` rows — reads them via
    ``list_daily_rollups``, never recomputes lane totals.

    Idempotent: every managed flag_type (see ``MANAGED_FLAG_TYPES``) is
    either upserted (if the rule holds) or deleted (if it does not), on
    every run — a re-run after a late correction converges to the current
    truth rather than accumulating stale rows.

    Returns a plain summary dict suitable as part of a scheduled-job result
    payload.
    """
    now = now or _now()

    rollup_rows = await list_daily_rollups(pool, local_date=local_date)
    seconds_by_lane: dict[str, float] = {r.lane: r.seconds for r in rollup_rows}

    dark_sources = await _dark_sources(pool, now=now)

    # feeder_dark
    feeder_dark_holds = bool(dark_sources)
    await _reconcile_flag(
        pool,
        local_date=local_date,
        flag_type=FLAG_FEEDER_DARK,
        should_exist=feeder_dark_holds,
        severity="warning",
        detail={"dark_sources": sorted(dark_sources)},
    )

    # sleep_missing
    sleep_source_dark = _SLEEP_SOURCE in dark_sources
    sleep_missing_holds = compute_sleep_missing(
        seconds_by_lane.get("sleep", 0), sleep_source_dark=sleep_source_dark
    )
    await _reconcile_flag(
        pool,
        local_date=local_date,
        flag_type=FLAG_SLEEP_MISSING,
        should_exist=sleep_missing_holds,
        severity="warning",
        detail={"seconds": seconds_by_lane.get("sleep", 0)},
    )

    # routine_break
    routine_source_dark = bool(_ROUTINE_BREAK_SOURCES & dark_sources)
    candidates = (
        []
        if routine_source_dark
        else await _routine_break_candidates(pool, local_date=local_date, default_timezone=timezone)
    )
    broken_routines = compute_routine_breaks(candidates, routine_source_dark=routine_source_dark)
    await _reconcile_flag(
        pool,
        local_date=local_date,
        flag_type=FLAG_ROUTINE_BREAK,
        should_exist=bool(broken_routines),
        severity="info",
        detail={"routines": broken_routines},
    )

    # lane_share_outlier
    trailing_by_date: dict[date, dict[str, float]] = {}
    for offset in range(1, LANE_SHARE_TRAILING_DAYS + 1):
        trailing_date = local_date - timedelta(days=offset)
        trailing_rows = await list_daily_rollups(pool, local_date=trailing_date)
        if trailing_rows:
            trailing_by_date[trailing_date] = {r.lane: r.seconds for r in trailing_rows}

    dark_lanes = [lane for lane in LANES if sources_for_lane(lane) & dark_sources]
    outliers = compute_lane_share_outliers(seconds_by_lane, trailing_by_date, dark_lanes=dark_lanes)
    await _reconcile_flag(
        pool,
        local_date=local_date,
        flag_type=FLAG_LANE_SHARE_OUTLIER,
        should_exist=bool(outliers),
        severity="info",
        detail={"lanes": outliers},
    )

    return {
        "local_date": local_date.isoformat(),
        "dark_sources": sorted(dark_sources),
        "flags": {
            FLAG_FEEDER_DARK: feeder_dark_holds,
            FLAG_SLEEP_MISSING: sleep_missing_holds,
            FLAG_ROUTINE_BREAK: bool(broken_routines),
            FLAG_LANE_SHARE_OUTLIER: bool(outliers),
        },
    }


__all__ = [
    "FEEDER_STALE_MULTIPLE",
    "FLAG_FEEDER_DARK",
    "FLAG_LANE_SHARE_OUTLIER",
    "FLAG_ROUTINE_BREAK",
    "FLAG_SLEEP_MISSING",
    "LANE_SHARE_OUTLIER_MULTIPLE",
    "LANE_SHARE_TRAILING_DAYS",
    "MANAGED_FLAG_TYPES",
    "MIN_EVIDENCE_FLOOR_SECONDS",
    "MIN_TRAILING_DAYS_FOR_MEDIAN",
    "SOURCE_CRON_MINUTES",
    "compute_lane_share_outliers",
    "compute_routine_breaks",
    "compute_sleep_missing",
    "evaluate_and_write_daily_flags",
    "is_source_dark",
]
