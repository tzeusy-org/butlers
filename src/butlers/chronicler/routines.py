"""Deterministic weekly routine miner (bu-whhll.9, epic bu-whhll Tier 2).

Mines N weeks (default 6) of already-projected ``chronicler.episodes`` for
stable weekday patterns — e.g. "Mon-Fri 09:30-19:30: continuous desk signals,
no movement, no gaming" — and upserts owner-reviewable rows into
``chronicler.routines`` (migration ``chronicler_018``).

**No LLM. Pure statistics.** This module has two layers:

- :func:`compute_routine_candidates` — a pure, dependency-light function over
  in-memory episode rows. No I/O, no clock reads beyond the caller-supplied
  date bounds. This is what the unit tests exercise directly with synthetic
  fixtures.
- :func:`mine_routines` — the async orchestrator that reads
  ``chronicler.episodes``/``chronicler.point_events`` from a real pool, calls
  the pure function, and upserts the result via
  ``storage.upsert_mined_routine``.

Signal model
------------
Only ``layer='activity'`` episodes count (tasks.md §4 / RFC 0014 §D7: intent
and evidence rows are never counted as lived time — see
``aggregations.lane_for_activity``). Every candidate episode is classified via
``aggregations.category_for`` into one of two roles for a given half-hour
local slot:

- **Desk signal** (``DESK_SIGNAL_CATEGORIES``) — music, tasks, conversations,
  social. Positive evidence the owner was at a desk/engaged.
- **Contradictor** (``CONTRADICTOR_CATEGORIES``) — travel (movement), gaming.
  Presence of either means the slot cannot be part of a "continuous desk
  signal, no movement, no gaming" routine, regardless of desk-signal
  co-occurrence.

A slot on a given local calendar date "qualifies" when it has >=1 desk signal
and zero contradictors. A slot is "stable" for a weekday when it qualifies on
at least ``MIN_SUPPORT_RATIO`` of the observed instances of that weekday (and
at least ``MIN_SUPPORT_COUNT`` of them, to avoid overfitting a single lucky
week). The longest contiguous run of stable slots per weekday becomes a
candidate window; weekdays sharing an identical window are grouped into one
routine with a combined ``dow_mask``.

Point events: v1 mines the raw ``chronicler.point_events`` count in the
window purely for ``evidence_summary`` telemetry (no dedicated point-event
category taxonomy exists yet — see ``aggregations.category_for``, which
classifies episodes only). As Tier 1 sensors (SSID presence, ActivityWatch)
mature into their own activity-layer episodes, they flow into the desk-signal
set automatically without a routines.py change.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from butlers.chronicler.aggregations import category_for
from butlers.chronicler.models import Layer
from butlers.chronicler.storage import upsert_mined_routine

# ── Tunables ────────────────────────────────────────────────────────────────

DEFAULT_WEEKS = 6
DEFAULT_TIMEZONE = "Asia/Singapore"

SLOT_MINUTES = 30
SLOTS_PER_DAY = (24 * 60) // SLOT_MINUTES  # 48

# A weekday slot must qualify (desk signal, no contradictor) on at least this
# fraction of observed instances of that weekday...
MIN_SUPPORT_RATIO = 0.7
# ...AND at least this many instances (guards small `weeks` values / partial
# windows from overfitting a single lucky week).
MIN_SUPPORT_COUNT = 3
# Minimum contiguous run length (in slots) to count as a candidate window —
# 4 slots = 2 hours. Shorter runs are noise, not a "workday".
MIN_WINDOW_SLOTS = 4

# Categories that mark a slot as "owner at the desk" for routine mining.
# bu-whhll.14 moved the owner's direct desk signals (focus_inferred /
# reading_inferred / activitywatch screen) from category 'tasks' to
# 'occupation', so 'occupation' is added here to keep them registering (else
# occupation inference would self-starve). ``mine_routines`` excludes the
# occupation adapter's OWN output source (``chronicler.occupation_inferred``,
# also category 'occupation') from its input, so this does NOT create a
# feedback loop — only the primary focus/reading/screen signals count. 'tasks'
# stays (butler-session work still counts as it did pre-split; re-scoping what
# butler activity means for owner-occupation inference is out of this
# presentation bead's scope).
DESK_SIGNAL_CATEGORIES: frozenset[str] = frozenset(
    {"music", "tasks", "conversations", "social", "occupation"}
)
CONTRADICTOR_CATEGORIES: frozenset[str] = frozenset({"travel", "gaming"})

_DOW_LABELS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_EVIDENCE_DESCRIPTION = "continuous desk signals, no movement, no gaming"


@dataclass(frozen=True)
class RoutineCandidate:
    """One mined routine candidate, ready for ``storage.upsert_mined_routine``."""

    dow_mask: int
    window_start_local: time
    window_end_local: time
    label: str
    support_count: int
    confidence: float
    evidence_summary: dict[str, Any] = field(default_factory=dict)


def _format_dow_ranges(dow_mask: int) -> str:
    """Render a dow_mask as contiguous-range labels, e.g. "Mon-Fri" or "Mon, Wed"."""
    days = [i for i in range(7) if dow_mask & (1 << i)]
    ranges: list[str] = []
    i = 0
    while i < len(days):
        j = i
        while j + 1 < len(days) and days[j + 1] == days[j] + 1:
            j += 1
        if j == i:
            ranges.append(_DOW_LABELS[days[i]])
        else:
            ranges.append(f"{_DOW_LABELS[days[i]]}-{_DOW_LABELS[days[j]]}")
        i = j + 1
    return ", ".join(ranges)


def _format_label(dow_mask: int, window_start_local: time, window_end_local: time) -> str:
    dow_str = _format_dow_ranges(dow_mask)
    return f"{dow_str} {window_start_local.strftime('%H:%M')}-{window_end_local.strftime('%H:%M')}"


def _slot_to_time(slot_index: int) -> time:
    """Convert a slot index (0..SLOTS_PER_DAY) to a local wall-clock time.

    ``slot_index == SLOTS_PER_DAY`` (a window running to end-of-day) cannot be
    represented as ``time(24, 0)``; it is clamped to the last representable
    instant of the day. Midnight-spanning routines are out of scope for v1
    (the migration's ``CHECK (window_end_local > window_start_local)``
    enforces same-day windows).
    """
    if slot_index >= SLOTS_PER_DAY:
        return time(23, 59, 59)
    minutes = slot_index * SLOT_MINUTES
    return time(hour=minutes // 60, minute=minutes % 60)


def compute_routine_candidates(
    episodes: Sequence[Mapping[str, Any]],
    *,
    mining_start_date: date,
    mining_end_date: date,
    timezone: str = DEFAULT_TIMEZONE,
) -> list[RoutineCandidate]:
    """Mine stable weekday desk-signal windows from a set of episode rows.

    Pure function: no I/O, no LLM, no side effects. ``episodes`` entries must
    provide ``source_name``, ``episode_type``, ``start_at`` (aware
    ``datetime``), ``end_at`` (aware ``datetime`` or ``None``), ``layer``, and
    optionally ``trigger_source`` (for ``core.sessions`` category
    resolution). Non-``activity``-layer rows are dropped immediately — this
    is the explicit layer-exclusion guard (tasks.md §4 / the #2918/#2921
    lesson): an intent (calendar) or evidence row must never contribute to a
    mined routine.

    ``mining_start_date``/``mining_end_date`` are local calendar dates
    (``mining_end_date`` exclusive) that bound which dates are considered —
    callers exclude the current, still-partial local day.

    Returns one :class:`RoutineCandidate` per distinct mined window,
    sorted by ``dow_mask`` then window start.
    """
    try:
        tzinfo = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone!r}") from exc

    activity_episodes = [
        ep
        for ep in episodes
        if str(ep.get("layer")) == Layer.ACTIVITY.value and ep.get("start_at") is not None
    ]

    dates: list[date] = []
    cursor = mining_start_date
    while cursor < mining_end_date:
        dates.append(cursor)
        cursor += timedelta(days=1)
    if not dates:
        return []

    # ── Per-date, per-slot desk-signal / contradictor flags ─────────────────
    date_slot_desk: dict[date, list[bool]] = {}
    date_slot_bad: dict[date, list[bool]] = {}
    for local_date in dates:
        desk = [False] * SLOTS_PER_DAY
        bad = [False] * SLOTS_PER_DAY
        local_midnight = datetime.combine(local_date, time.min, tzinfo)
        for slot_idx in range(SLOTS_PER_DAY):
            slot_start_local = local_midnight + timedelta(minutes=slot_idx * SLOT_MINUTES)
            slot_start_utc = slot_start_local.astimezone(UTC)
            slot_end_utc = slot_start_utc + timedelta(minutes=SLOT_MINUTES)
            for ep in activity_episodes:
                ep_start = ep["start_at"]
                ep_end = ep.get("end_at") or ep_start
                if ep_start >= slot_end_utc or ep_end <= slot_start_utc:
                    continue
                category = category_for(
                    ep["source_name"],
                    ep["episode_type"],
                    trigger_source=ep.get("trigger_source"),
                )
                if category in CONTRADICTOR_CATEGORIES:
                    bad[slot_idx] = True
                elif category in DESK_SIGNAL_CATEGORIES:
                    desk[slot_idx] = True
        date_slot_desk[local_date] = desk
        date_slot_bad[local_date] = bad

    # ── Group dates by weekday, find stable contiguous runs ─────────────────
    by_dow: dict[int, list[date]] = defaultdict(list)
    for local_date in dates:
        by_dow[local_date.weekday()].append(local_date)

    # window signature (start, end) -> aggregated candidate state
    grouped: dict[tuple[time, time], dict[str, Any]] = {}

    for dow, dow_dates in sorted(by_dow.items()):
        total_days = len(dow_dates)
        if total_days == 0:
            continue

        qualifying_counts = [0] * SLOTS_PER_DAY
        for local_date in dow_dates:
            desk = date_slot_desk[local_date]
            bad = date_slot_bad[local_date]
            for i in range(SLOTS_PER_DAY):
                if desk[i] and not bad[i]:
                    qualifying_counts[i] += 1

        stable = [
            (qualifying_counts[i] / total_days) >= MIN_SUPPORT_RATIO
            and qualifying_counts[i] >= MIN_SUPPORT_COUNT
            for i in range(SLOTS_PER_DAY)
        ]

        best_run: tuple[int, int] | None = None
        run_start: int | None = None
        for i in range(SLOTS_PER_DAY + 1):
            is_stable = stable[i] if i < SLOTS_PER_DAY else False
            if is_stable and run_start is None:
                run_start = i
            elif not is_stable and run_start is not None:
                run_len = i - run_start
                if run_len >= MIN_WINDOW_SLOTS and (
                    best_run is None or run_len > (best_run[1] - best_run[0])
                ):
                    best_run = (run_start, i)
                run_start = None
        if best_run is None:
            continue

        start_slot, end_slot = best_run
        window_start_local = _slot_to_time(start_slot)
        window_end_local = _slot_to_time(end_slot)

        support_count = sum(
            1
            for local_date in dow_dates
            if all(
                date_slot_desk[local_date][i] and not date_slot_bad[local_date][i]
                for i in range(start_slot, end_slot)
            )
        )

        key = (window_start_local, window_end_local)
        agg = grouped.setdefault(key, {"dow_mask": 0, "support_count": 0, "total_days": 0})
        agg["dow_mask"] |= 1 << dow
        agg["support_count"] += support_count
        agg["total_days"] += total_days

    results: list[RoutineCandidate] = []
    for (window_start_local, window_end_local), agg in grouped.items():
        total_days = agg["total_days"]
        support_count = agg["support_count"]
        confidence = round(support_count / total_days, 4) if total_days else 0.0
        dow_mask = agg["dow_mask"]
        results.append(
            RoutineCandidate(
                dow_mask=dow_mask,
                window_start_local=window_start_local,
                window_end_local=window_end_local,
                label=_format_label(dow_mask, window_start_local, window_end_local),
                support_count=support_count,
                confidence=confidence,
                evidence_summary={
                    "desk_signal_categories": sorted(DESK_SIGNAL_CATEGORIES),
                    "contradictor_categories": sorted(CONTRADICTOR_CATEGORIES),
                    "days_observed": total_days,
                    "days_supporting": support_count,
                    "description": _EVIDENCE_DESCRIPTION,
                },
            )
        )

    return sorted(results, key=lambda c: (c.dow_mask, c.window_start_local))


async def mine_routines(
    pool: asyncpg.Pool,
    *,
    weeks: int = DEFAULT_WEEKS,
    timezone: str = DEFAULT_TIMEZONE,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mine N weeks of chronicler activity episodes and upsert mined routines.

    Reads directly from ``chronicler.episodes``/``chronicler.point_events``
    (chronicler reading its own schema, same convention as
    ``adapters/focus.py``). The mining window is
    ``[today_local - weeks*7 days, today_local)`` — the current, still-partial
    local day is always excluded so a half-finished workday cannot suppress
    an otherwise-stable pattern.

    Returns a plain summary dict suitable as a scheduled-job result payload.
    """
    if not isinstance(weeks, int) or isinstance(weeks, bool) or weeks <= 0:
        raise ValueError(f"weeks must be a positive integer, got {weeks!r}")
    try:
        tzinfo = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone!r}") from exc

    now = now or datetime.now(UTC)
    local_now = now.astimezone(tzinfo)
    mining_end_date = local_now.date()
    mining_start_date = mining_end_date - timedelta(days=weeks * 7)

    window_start_utc = datetime.combine(mining_start_date, time.min, tzinfo).astimezone(UTC)
    window_end_utc = datetime.combine(mining_end_date, time.min, tzinfo).astimezone(UTC)

    rows = await pool.fetch(
        """
        SELECT source_name, episode_type, start_at, end_at, layer,
               payload->>'trigger_source' AS trigger_source
        FROM episodes
        WHERE tombstone_at IS NULL
          AND layer = 'activity'
          -- The miner reads PRIMARY activity signals only, never its own
          -- derived occupation blocks (bu-whhll.14): occupation_inferred is
          -- category 'occupation', now a desk signal, so feeding it back would
          -- make inferred occupation windows self-perpetuate.
          AND source_name != 'chronicler.occupation_inferred'
          AND start_at < $2
          AND (end_at IS NULL OR end_at > $1)
        ORDER BY start_at ASC
        """,
        window_start_utc,
        window_end_utc,
    )
    episodes = [dict(r) for r in rows]

    point_event_count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM point_events
        WHERE occurred_at >= $1 AND occurred_at < $2
        """,
        window_start_utc,
        window_end_utc,
    )

    candidates = compute_routine_candidates(
        episodes,
        mining_start_date=mining_start_date,
        mining_end_date=mining_end_date,
        timezone=timezone,
    )

    routines_written = 0
    for candidate in candidates:
        evidence_summary = {
            **candidate.evidence_summary,
            "weeks_analyzed": weeks,
            "point_events_observed": point_event_count,
        }
        await upsert_mined_routine(
            pool,
            dow_mask=candidate.dow_mask,
            window_start_local=candidate.window_start_local,
            window_end_local=candidate.window_end_local,
            timezone=timezone,
            label=candidate.label,
            support_count=candidate.support_count,
            confidence=candidate.confidence,
            evidence_summary=evidence_summary,
        )
        routines_written += 1

    return {
        "weeks": weeks,
        "timezone": timezone,
        "window_start": window_start_utc.isoformat(),
        "window_end": window_end_utc.isoformat(),
        "episodes_considered": len(episodes),
        "point_events_observed": point_event_count,
        "candidates_found": len(candidates),
        "routines_written": routines_written,
    }


__all__ = [
    "CONTRADICTOR_CATEGORIES",
    "DEFAULT_TIMEZONE",
    "DEFAULT_WEEKS",
    "DESK_SIGNAL_CATEGORIES",
    "MIN_SUPPORT_COUNT",
    "MIN_SUPPORT_RATIO",
    "MIN_WINDOW_SLOTS",
    "RoutineCandidate",
    "SLOTS_PER_DAY",
    "SLOT_MINUTES",
    "compute_routine_candidates",
    "mine_routines",
]
