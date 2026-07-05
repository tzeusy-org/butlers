"""Deterministic daily rollup materializer (bu-u30as, telemetry-distillation
bead 3, design doc §3.3/§6.3).

Aggregates the day's ``activity``-layer episodes into a persisted per-lane
summary (``chronicler.daily_rollups``), so a trend view or anomaly rule can
read a cheap, already-computed row instead of re-scanning raw episodes on
every request. Structurally mirrors ``routines.py``'s split:

- :func:`compute_daily_lane_rollup` — a pure, dependency-light function over
  in-memory episode rows. No I/O, no clock reads. This is what the unit
  tests exercise directly, and it is deliberately the **only** place lane
  totals are computed: it calls ``aggregations.lane_for_activity`` and
  ``aggregations.union_seconds`` directly, exactly as
  ``GET /aggregate/by-category`` does, so the rollup and the live endpoint
  can never diverge (design doc §2 principle 2 — the bu-whhll.1-class
  KPI-divergence bug this must not reopen). See
  ``tests/integration/test_daily_rollups_integration.py`` for the real-
  Postgres bit-for-bit regression against the live endpoint.
- :func:`materialize_daily_rollups` — the async orchestrator that reads
  ``chronicler.episodes`` from a real pool for each fully-elapsed local day
  in the lookback window, calls the pure function, and upserts the result
  via ``storage.upsert_daily_rollup``.

No LLM call — pure deterministic aggregation only (RFC 0014 §D5).

Scope note: this module only writes ``daily_rollups``. The
``daily_rollup_flags`` table (migration ``chronicler_019``) exists for the
anomaly-flag rules that are bead 4's scope (design doc §3.4) — nothing in
this module writes to it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from butlers.chronicler.aggregations import LANES, lane_for_activity, union_seconds
from butlers.chronicler.storage import upsert_daily_rollup

DEFAULT_TIMEZONE = "Asia/Singapore"

# How many trailing fully-elapsed local days to (re-)materialize on each run.
# Re-processing recent days on every run (rather than tracking a strict
# watermark) is what makes late-arriving corrections/overrides converge —
# the upsert is idempotent, so re-computing an already-rolled-up day is a
# no-op cost, not a correctness risk. 7 days comfortably covers the owner
# correction window without re-scanning the full episode history every run.
DEFAULT_LOOKBACK_DAYS = 7

# Same default privacy filter GET /aggregate/by-category applies when the
# caller omits privacy_tier: include normal + sensitive, exclude restricted.
# Hardcoded (not parameterized) because the rollup has no caller-supplied
# query params to vary it by — matching the live endpoint's default is what
# keeps the two surfaces from diverging.
_DEFAULT_PRIVACY_TIERS: tuple[str, ...] = ("normal", "sensitive")


def _now() -> datetime:
    """Wall-clock now, isolated for test patching."""
    return datetime.now(UTC)


def compute_daily_lane_rollup(
    episodes: Sequence[Mapping[str, Any]],
    *,
    day_start_utc: datetime,
    day_end_utc: datetime,
) -> dict[str, dict[str, Any]]:
    """Aggregate one closed local day's episodes into per-lane totals.

    Pure function: no I/O, no LLM, no side effects. ``episodes`` entries must
    provide ``source_name``, ``episode_type``, ``start_at`` (aware
    ``datetime``), ``end_at`` (aware ``datetime`` or ``None``), ``layer``,
    and optionally ``trigger_source`` (for ``core.sessions`` category
    resolution) — the same row shape ``GET /aggregate/by-category`` reads
    from ``v_episodes_corrected``. Callers are expected to have already
    applied the same privacy/tombstone filter the live endpoint defaults to
    (see ``_DEFAULT_PRIVACY_TIERS``); this function only clips and
    aggregates.

    Each episode's span is clipped to ``[day_start_utc, day_end_utc)``; an
    episode with no overlap is dropped. Only ``activity``-layer episodes
    that resolve to a lane count (``lane_for_activity`` returns ``None`` for
    ``intent``/``evidence`` rows and unmapped sources) — identical gating to
    the live endpoint.

    Returns a dict keyed by every lane in ``aggregations.LANES`` (zero-filled
    for lanes with no activity that day, so downstream consumers — anomaly
    rules, the future rollups API — never need to distinguish "no row" from
    "zero seconds"), each value ``{"seconds": float, "episode_count": int}``.
    """
    lane_intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    lane_episode_counts: dict[str, int] = defaultdict(int)

    for ep in episodes:
        ep_start: datetime = ep["start_at"]
        ep_end: datetime | None = ep.get("end_at")
        ep_end_resolved = ep_end if ep_end is not None else day_end_utc

        overlap_start = max(ep_start, day_start_utc)
        overlap_end = min(ep_end_resolved, day_end_utc)
        if overlap_end <= overlap_start:
            continue

        lane = lane_for_activity(
            ep["layer"],
            ep["source_name"],
            ep["episode_type"],
            trigger_source=ep.get("trigger_source"),
        )
        if lane is None:
            continue

        lane_intervals[lane].append((overlap_start, overlap_end))
        lane_episode_counts[lane] += 1

    return {
        lane: {
            "seconds": union_seconds(lane_intervals.get(lane, [])),
            "episode_count": lane_episode_counts.get(lane, 0),
        }
        for lane in LANES
    }


def _local_day_bounds_utc(local_date: date, tzinfo: ZoneInfo) -> tuple[datetime, datetime]:
    day_start_local = datetime.combine(local_date, time.min, tzinfo)
    day_end_local = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo)
    return day_start_local.astimezone(UTC), day_end_local.astimezone(UTC)


async def materialize_daily_rollups(
    pool: asyncpg.Pool,
    *,
    timezone: str = DEFAULT_TIMEZONE,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Materialize ``daily_rollups`` for every fully-elapsed local day in the
    trailing ``lookback_days`` window.

    Only fully-elapsed local calendar days are materialized (the current,
    still-partial local day is always excluded) — same "only project
    fully-elapsed windows" convention ``occupation.py`` uses. Re-processing
    the trailing window on every run (rather than tracking a strict
    watermark) is intentional: the upsert is idempotent on
    ``(local_date, lane)``, so a late-arriving correction/override is picked
    up on the next run with no special-casing.

    Reads directly from ``chronicler.episodes`` (chronicler reading its own
    schema, same convention as ``routines.mine_routines``), applying the
    identical default window/tombstone/privacy filter
    ``GET /aggregate/by-category`` applies when its caller omits
    ``privacy_tier``/``include_tombstoned``.

    Returns a plain summary dict suitable as a scheduled-job result payload.
    """
    if not isinstance(lookback_days, int) or isinstance(lookback_days, bool) or lookback_days <= 0:
        raise ValueError(f"lookback_days must be a positive integer, got {lookback_days!r}")
    try:
        tzinfo = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone!r}") from exc

    now = now or _now()
    local_now = now.astimezone(tzinfo)
    today_local = local_now.date()
    first_date = today_local - timedelta(days=lookback_days)

    days_processed: list[str] = []
    lanes_written = 0

    local_date = first_date
    while local_date < today_local:
        day_start_utc, day_end_utc = _local_day_bounds_utc(local_date, tzinfo)
        # Only fully-elapsed windows. Always true for local_date < today_local,
        # but kept as an explicit guard (mirrors occupation.py) in case a
        # future caller widens the loop bounds.
        if day_end_utc <= now:
            privacy_placeholders = ", ".join(
                f"${i + 3}" for i in range(len(_DEFAULT_PRIVACY_TIERS))
            )
            rows = await pool.fetch(
                f"""
                SELECT
                    source_name,
                    episode_type,
                    start_at,
                    end_at,
                    layer,
                    payload->>'trigger_source' AS trigger_source
                FROM v_episodes_corrected
                WHERE start_at < $2
                  AND (end_at IS NULL OR end_at > $1)
                  AND tombstone_at IS NULL
                  AND privacy IN ({privacy_placeholders})
                """,
                day_start_utc,
                day_end_utc,
                *_DEFAULT_PRIVACY_TIERS,
            )
            episodes = [dict(r) for r in rows]

            rollup = compute_daily_lane_rollup(
                episodes, day_start_utc=day_start_utc, day_end_utc=day_end_utc
            )
            for lane, totals in rollup.items():
                await upsert_daily_rollup(
                    pool,
                    local_date=local_date,
                    lane=lane,
                    seconds=round(totals["seconds"]),
                    episode_count=totals["episode_count"],
                    timezone=timezone,
                )
                lanes_written += 1
            days_processed.append(local_date.isoformat())

        local_date += timedelta(days=1)

    return {
        "timezone": timezone,
        "lookback_days": lookback_days,
        "days_processed": days_processed,
        "lanes_written": lanes_written,
    }


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_TIMEZONE",
    "compute_daily_lane_rollup",
    "materialize_daily_rollups",
]
