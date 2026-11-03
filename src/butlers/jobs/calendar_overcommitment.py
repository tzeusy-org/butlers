"""Calendar overcommitment owner-condition producer (bu-vcjgo8, REQ-owner-condition-ledger-007).

A supervised dashboard-api loop that scans the owner's forward calendar window with the radar's
own read model (:func:`~butlers.api.read_models.calendar_workspace.query_calendar_conflicts`) and
reconciles its qualifying issues into ``public.owner_conditions`` under source
``calendar:overcommitment``. It runs here, beside ``calendar_sync_deadman``, for the same reason:
the scan fans out over every calendar-enabled butler schema, which no single butler pool can see.
Deterministic: no model, no obligation admission.

What qualifies
--------------
``overlap`` and ``overloaded_day`` issues with ``severity == "warning"``. ``back_to_back`` never
qualifies, even a ``warning`` chain: it is a comfort signal, not overcommitment. Severity, hours
and counts are evidence; identity is ``{kind, pair_id}`` or ``{kind, date}``.

Identity seam (task 1.1)
------------------------
``calendar_event_instances.id`` is stable across a resync of one schema's copy (the projection
upserts on ``(event_id, origin_instance_ref)``), but the same provider event is projected into
every calendar butler's schema and the ``origin_ref`` dedup keeps the most recently synced copy.
The surviving ``entry_id`` therefore rotates between schemas from one scan to the next, and with
it the radar's ``overlap_pair_id``. The pair identity is built instead from each entry's provider
identity (``CalendarConflictScan.entry_identity_keys``). A dedup-store outage makes the collapse
fall back to defaults and can change which events exist at all, so such a scan is not reconciled.

Freshness seam (task 1.2)
-------------------------
A source whose sync stopped keeps its rows, so a stale scan can keep observing a conflict the
provider already cancelled, or miss a new one; it cannot fabricate an absence, so it never
resolves a live conflict. Sync death is already its own condition (``calendar_sync_deadman``), so
fresh sources are not an extra completeness gate here.

Completeness
------------
A scan is complete only when every schema's fan-out answered (``scan.available``). A partial scan
confirms what it saw and never resolves by omission. A tick that cannot measure at all (no
Switchboard pool, unreadable owner timezone, scan raised, dedup store unread) reconciles nothing.
A ledger failure is logged and the loop waits for its next tick.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from butlers.api.db import DatabaseManager
from butlers.api.read_models.calendar_workspace import (
    CalendarConflictScan,
    query_calendar_conflicts,
)
from butlers.core.general_settings import load_general_settings
from butlers.core.job_health import mark_tick_incomplete, record_tick
from butlers.core.owner_conditions import Observation, compute_fingerprint, reconcile_snapshot
from butlers.core.temporal.conflicts import DetectedIssue, overlap_pair_id

logger = logging.getLogger(__name__)

CONDITION_SOURCE = "calendar:overcommitment"
IDENTITY_VERSION = 1
INITIAL_GRACE_S = 3600.0

DEFAULT_CHECK_INTERVAL_S = 1800
CHECK_INTERVAL_ENV = "CALENDAR_OVERCOMMITMENT_CHECK_INTERVAL_S"
DEFAULT_LOOKAHEAD_DAYS = 7
MAX_LOOKAHEAD_DAYS = 90
LOOKAHEAD_DAYS_ENV = "CALENDAR_OVERCOMMITMENT_LOOKAHEAD_DAYS"

_QUALIFYING_KINDS = frozenset({"overlap", "overloaded_day"})


def resolve_lookahead_days() -> int:
    """Read the lookahead from the environment; anything outside 1..90 uses the default."""
    raw = os.environ.get(LOOKAHEAD_DAYS_ENV)
    if raw is None:
        return DEFAULT_LOOKAHEAD_DAYS
    try:
        days = int(raw)
    except ValueError:
        days = 0
    if not 1 <= days <= MAX_LOOKAHEAD_DAYS:
        logger.warning(
            "%s=%r is not a whole number of days in 1..%d; using %d",
            LOOKAHEAD_DAYS_ENV,
            raw,
            MAX_LOOKAHEAD_DAYS,
            DEFAULT_LOOKAHEAD_DAYS,
        )
        return DEFAULT_LOOKAHEAD_DAYS
    return days


def owner_window(now: datetime, tz: ZoneInfo, days: int) -> tuple[datetime, datetime]:
    """``[owner-local midnight today, + days)``: today's overloaded-day total stays whole."""
    today = now.astimezone(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    return start, datetime.combine(today + timedelta(days=days), time.min, tzinfo=tz)


def stable_pair_id(identity_a: str, identity_b: str) -> str:
    """Order-free pair id over provider identities (the radar's UUID5, other inputs)."""
    return str(overlap_pair_id(identity_a, identity_b))


def overcommitment_fingerprint(facts: dict[str, str]) -> str:
    return compute_fingerprint(CONDITION_SOURCE, IDENTITY_VERSION, facts)


def build_overcommitment_observations(scan: CalendarConflictScan) -> list[Observation]:
    """Title-free observations for the scan's qualifying issues.

    Summary and metadata carry the kind, owner-local date, severity, and either the overlap
    minutes with the two entry ids or the meeting hours with the event count. Never a title,
    attendee or location.
    """
    observations: list[Observation] = []
    for issue in scan.issues:
        if issue.kind not in _QUALIFYING_KINDS or issue.severity != "warning":
            continue
        if issue.kind == "overlap":
            observations.append(_overlap_observation(issue, scan.entry_identity_keys))
        else:
            observations.append(_overloaded_day_observation(issue))
    return observations


def _overlap_observation(issue: DetectedIssue, identity_keys: dict[str, str]) -> Observation:
    a, b = issue.events[0], issue.events[1]
    minutes = int((min(a.end_at, b.end_at) - max(a.start_at, b.start_at)).total_seconds() // 60)
    pair_id = stable_pair_id(
        identity_keys.get(a.entry_id, f"instance\x01{a.entry_id}"),
        identity_keys.get(b.entry_id, f"instance\x01{b.entry_id}"),
    )
    return Observation(
        fingerprint=overcommitment_fingerprint({"kind": "overlap", "pair_id": pair_id}),
        identity_version=IDENTITY_VERSION,
        summary=f"Double-booked on {issue.date}: two events overlap by {minutes} min",
        metadata={
            "kind": "overlap",
            "date": issue.date,
            "severity": issue.severity,
            "overlap_minutes": minutes,
            "entry_ids": sorted([a.entry_id, b.entry_id]),
        },
    )


def _overloaded_day_observation(issue: DetectedIssue) -> Observation:
    seconds = sum((e.end_at - e.start_at).total_seconds() for e in issue.events)
    hours = round(seconds / 3600, 1)
    count = len(issue.events)
    return Observation(
        fingerprint=overcommitment_fingerprint({"kind": "overloaded_day", "date": issue.date}),
        identity_version=IDENTITY_VERSION,
        summary=f"Overloaded day {issue.date}: {hours:.1f} h of meetings across {count} events",
        metadata={
            "kind": "overloaded_day",
            "date": issue.date,
            "severity": issue.severity,
            "meeting_hours": hours,
            "event_count": count,
        },
    )


def _skip(reason: str) -> dict[str, Any]:
    mark_tick_incomplete()
    logger.warning("calendar overcommitment: tick not reconciled (%s)", reason)
    return {"reconciled": False, "reason": reason}


async def run_calendar_overcommitment_check(
    db: DatabaseManager,
    *,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One tick: scan the owner window and reconcile it. Never raises."""
    try:
        pool = db.pool("switchboard")
    except KeyError:
        return _skip("no_pool")
    try:
        tz = ZoneInfo((await load_general_settings(pool))["timezone"])
    except Exception:
        logger.warning("calendar overcommitment: owner timezone unreadable", exc_info=True)
        return _skip("timezone_unavailable")
    start, end = owner_window(now or datetime.now(UTC), tz, lookahead_days)
    try:
        scan = await query_calendar_conflicts(db, start=start, end=end, display_tz=tz)
    except Exception:
        logger.warning("calendar overcommitment: conflict scan raised", exc_info=True)
        return _skip("scan_failed")
    if not scan.dedup_available:
        return _skip("dedup_unavailable")
    if not scan.available:
        mark_tick_incomplete()

    observations = build_overcommitment_observations(scan)
    try:
        transitions = await reconcile_snapshot(
            pool,
            source=CONDITION_SOURCE,
            observations=observations,
            snapshot_complete=scan.available,
            initial_grace_seconds=INITIAL_GRACE_S,
        )
    except Exception:
        logger.warning("calendar overcommitment: reconciliation failed", exc_info=True)
        return _skip("reconcile_failed")
    return {
        "reconciled": True,
        "complete": scan.available,
        "observed": len(observations),
        "transitions": [t.transition for t in transitions],
    }


async def run_calendar_overcommitment_loop(
    db: DatabaseManager,
    *,
    interval_s: float = DEFAULT_CHECK_INTERVAL_S,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> None:
    """Run :func:`run_calendar_overcommitment_check` every ``interval_s`` until cancelled.

    Sleeps first, like ``run_calendar_sync_deadman_loop``. One bad tick is logged and
    swallowed so it never kills the loop.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_calendar_overcommitment_check(db, lookahead_days=lookahead_days)
            await record_tick(failed=not summary["reconciled"])
            logger.info("calendar_overcommitment_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            await record_tick(failed=True)
            logger.exception("calendar_overcommitment_check: tick failed")
