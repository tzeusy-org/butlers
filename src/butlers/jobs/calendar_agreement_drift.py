"""Changed since you said yes: the agreement-drift reconciler (bu-5ii4gj.10 slice A-S2).

A supervised dashboard-api loop, beside ``calendar_overcommitment``, that compares every occurrence
the owner accepted (``calendar_owner_agreements``, one table per calendar butler schema) with what
the calendar says now, and reconciles one ``public.owner_conditions`` episode per materially
changed occurrence under source ``calendar:changed_since_accepted``. Deterministic: no model.

Material change
---------------
The start moved by 15 minutes or more, the location digest changed, or every other attendee was
removed. A change to the title or description alone never qualifies: neither is compared. The
join-link digest is reserved and compared once the provider mapping carries conference data.

Where the comparison reads
--------------------------
Each schema's agreement row carries the agreed terms and ``current_*`` terms that follow the
projection. The connector's revision journal can be newer than a projection that has not synced
yet, so the job also reads the journal's newest version of each checked event through the narrow
``public.read_calendar_event_heads`` definer (status, start, end and observation time only). A head
observed after the projection last wrote overrides the projected start and end, and a head that says
the event is cancelled ends the agreement: nothing is observed for it.

Identity and copies
-------------------
``{provider identity, original start}`` where the provider identity is the calendar event's
``origin_ref``. The same provider event is projected into every calendar butler's schema, so copies
collapse to one condition; the copy from the alphabetically first butler is the one reported.

Completeness
------------
A tick that cannot measure reconciles nothing and records an incomplete tick: no Switchboard pool,
a schema fan-out that raised outright, a journal read that failed, or a stale or unreadable
calendar source. A partial fan-out confirms what it saw and never resolves by omission. Nothing
here ever resolves a condition because the journal could not be read.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.core.job_health import mark_tick_incomplete, record_tick
from butlers.core.owner_conditions import Observation, compute_fingerprint, reconcile_snapshot
from butlers.core.temporal.invitation_quarantine import quarantined_invitation_sql
from butlers.jobs.calendar_sync_deadman import compute_calendar_sync_report
from butlers.modules.calendar.agreements import attendees_digest

logger = logging.getLogger(__name__)

CONDITION_SOURCE = "calendar:changed_since_accepted"
IDENTITY_VERSION = 1
INITIAL_GRACE_S = 0.0
DEFAULT_CHECK_INTERVAL_S = 600
MATERIAL_START_MOVE = timedelta(minutes=15)
_EMPTY_ATTENDEES = attendees_digest([])

_AGREEMENTS_SQL = f"""
SELECT a.occurrence_start, a.agreed_starts_at, a.agreed_ends_at, a.location_digest,
       a.attendees_digest, a.current_starts_at, a.current_ends_at, a.current_location_digest,
       a.current_attendees_digest, a.current_observed_at, ce.id AS event_id, ce.origin_ref
  FROM calendar_owner_agreements a
  JOIN calendar_events ce ON ce.id = a.event_id
 WHERE ce.origin_ref IS NOT NULL
   AND ce.status <> 'cancelled'
   AND NOT {quarantined_invitation_sql("ce")}
   AND GREATEST(a.agreed_starts_at, a.current_starts_at) > now()
"""


@dataclass(frozen=True)
class Head:
    """The journal's newest observed version of one provider event (times and status only)."""

    status: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    observed_at: datetime


@dataclass(frozen=True)
class Agreed:
    """One accepted occurrence in one butler's schema, as read this tick."""

    butler: str
    event_id: str
    origin_ref: str
    occurrence_start: datetime
    agreed_start: datetime
    agreed_end: datetime
    agreed_location: str | None
    agreed_attendees: str
    current_start: datetime
    current_end: datetime
    current_location: str | None
    current_attendees: str
    current_observed_at: datetime


def material_reasons(item: Agreed, head: Head | None) -> tuple[list[str], int]:
    """The material changes since acceptance, and how many minutes the start moved.

    Empty when the event is cancelled in the journal (the agreement is over, not changed). A
    journal head observed after the projection last wrote overrides the projected times.
    """
    start, end = item.current_start, item.current_end
    if head is not None:
        if head.status == "cancelled":
            return [], 0
        if head.observed_at > item.current_observed_at and head.starts_at is not None:
            start = head.starts_at
            end = head.ends_at or end
    moved = abs(start - item.agreed_start)
    reasons: list[str] = []
    if moved >= MATERIAL_START_MOVE:
        reasons.append("moved")
    if item.current_location != item.agreed_location:
        reasons.append("relocated")
    if (
        item.current_attendees != item.agreed_attendees
        and item.current_attendees == _EMPTY_ATTENDEES
        and item.agreed_attendees != _EMPTY_ATTENDEES
    ):
        reasons.append("attendees_removed")
    return reasons, int(moved.total_seconds() // 60)


def fingerprint(origin_ref: str, occurrence_start: datetime) -> str:
    return compute_fingerprint(
        CONDITION_SOURCE,
        IDENTITY_VERSION,
        {"provider_identity": origin_ref, "original_start": occurrence_start.isoformat()},
    )


def build_observations(
    items: Iterable[Agreed], heads: Mapping[str, Head]
) -> tuple[list[Observation], int]:
    """One title-free observation per changed occurrence, copies across schemas collapsed.

    Returns the observations and how many accepted occurrences were checked.
    """
    chosen: dict[tuple[str, datetime], Agreed] = {}
    for item in sorted(items, key=lambda i: i.butler):
        chosen.setdefault((item.origin_ref, item.occurrence_start), item)
    observations: list[Observation] = []
    for (origin_ref, occurrence_start), item in chosen.items():
        reasons, moved_minutes = material_reasons(item, heads.get(origin_ref))
        if not reasons:
            continue
        observations.append(
            Observation(
                fingerprint=fingerprint(origin_ref, occurrence_start),
                identity_version=IDENTITY_VERSION,
                summary="An event you accepted changed: "
                + ", ".join(r.replace("_", " ") for r in reasons),
                metadata={
                    "butler": item.butler,
                    "event_id": item.event_id,
                    "original_start": occurrence_start.isoformat(),
                    "agreed_start": item.agreed_start.isoformat(),
                    "reasons": reasons,
                    "moved_minutes": moved_minutes,
                },
            )
        )
    return observations, len(chosen)


async def read_journal_heads(pool: asyncpg.Pool, origin_refs: list[str]) -> dict[str, Head]:
    """The journal's newest version of each event through the narrow definer. Raises on failure."""
    heads: dict[str, Head] = {}
    for offset in range(0, len(origin_refs), 500):
        rows = await pool.fetch(
            "SELECT event_id, status, starts_at, ends_at, observed_at "
            "FROM public.read_calendar_event_heads($1::text[])",
            origin_refs[offset : offset + 500],
        )
        for row in rows:
            heads[row["event_id"]] = Head(
                row["status"], row["starts_at"], row["ends_at"], row["observed_at"]
            )
    return heads


def _skip(reason: str) -> dict[str, Any]:
    mark_tick_incomplete()
    logger.warning("calendar agreement drift: tick not reconciled (%s)", reason)
    return {"reconciled": False, "reason": reason}


def _agreed(butler: str, row: Mapping[str, Any]) -> Agreed:
    return Agreed(
        butler=butler,
        event_id=str(row["event_id"]),
        origin_ref=row["origin_ref"],
        occurrence_start=row["occurrence_start"],
        agreed_start=row["agreed_starts_at"],
        agreed_end=row["agreed_ends_at"],
        agreed_location=row["location_digest"],
        agreed_attendees=row["attendees_digest"],
        current_start=row["current_starts_at"],
        current_end=row["current_ends_at"],
        current_location=row["current_location_digest"],
        current_attendees=row["current_attendees_digest"],
        current_observed_at=row["current_observed_at"],
    )


async def run_calendar_agreement_drift_check(db: DatabaseManager) -> dict[str, Any]:
    """One tick: compare accepted occurrences with the calendar and reconcile. Never raises."""
    try:
        pool = db.pool("switchboard")
    except KeyError:
        return _skip("no_pool")
    report = await compute_calendar_sync_report(db)
    if not report.is_available or report.is_stale:
        return _skip("source_stale" if report.is_available else "sources_unreadable")
    try:
        results, failed = await db.fan_out_with_status(
            _AGREEMENTS_SQL, (), butler_names=db.butlers_with_module("calendar")
        )
    except Exception:
        logger.warning("calendar agreement drift: agreement read raised", exc_info=True)
        return _skip("agreements_unreadable")
    items = [_agreed(butler, dict(row)) for butler, rows in results.items() for row in rows]
    try:
        heads = await read_journal_heads(pool, sorted({i.origin_ref for i in items}))
    except Exception:
        logger.warning("calendar agreement drift: journal read failed", exc_info=True)
        return _skip("journal_unavailable")
    complete = not failed and not report.failed_butlers
    if not complete:
        mark_tick_incomplete()
    observations, checked = build_observations(items, heads)
    try:
        transitions = await reconcile_snapshot(
            pool,
            source=CONDITION_SOURCE,
            observations=observations,
            snapshot_complete=complete,
            initial_grace_seconds=INITIAL_GRACE_S,
        )
    except Exception:
        logger.warning("calendar agreement drift: reconciliation failed", exc_info=True)
        return _skip("reconcile_failed")
    return {
        "reconciled": True,
        "complete": complete,
        "checked": checked,
        "observed": len(observations),
        "transitions": [t.transition for t in transitions],
    }


async def run_calendar_agreement_drift_loop(
    db: DatabaseManager, *, interval_s: float = DEFAULT_CHECK_INTERVAL_S
) -> None:
    """Run :func:`run_calendar_agreement_drift_check` every ``interval_s`` until cancelled."""
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_calendar_agreement_drift_check(db)
            await record_tick(failed=not summary["reconciled"])
            logger.info("calendar_agreement_drift_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            await record_tick(failed=True)
            logger.exception("calendar_agreement_drift_check: tick failed")


__all__ = [
    "CONDITION_SOURCE",
    "Agreed",
    "Head",
    "build_observations",
    "material_reasons",
    "run_calendar_agreement_drift_check",
    "run_calendar_agreement_drift_loop",
]
