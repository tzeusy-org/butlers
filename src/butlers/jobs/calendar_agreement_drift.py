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
from datetime import UTC, datetime, timedelta
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
TIMED_WITHIN = timedelta(hours=24)
CANDIDATE_PRIORITY = 60
CANDIDATE_CATEGORY = "calendar_changed"
_EMPTY_ATTENDEES = attendees_digest([])

_AGREEMENTS_SQL = f"""
SELECT a.occurrence_start, a.agreed_starts_at, a.agreed_ends_at, a.location_digest,
       a.attendees_digest, a.current_starts_at, a.current_ends_at, a.current_location_digest,
       a.current_attendees_digest, a.current_observed_at, ce.id AS event_id, ce.origin_ref,
       ce.title,
       (SELECT max(c.last_success_at) FROM calendar_sync_cursors c
         WHERE c.source_id = ce.source_id) AS synced_at,
       CASE WHEN ce.status = 'cancelled' THEN 'cancelled'
            WHEN a.ended_at IS NOT NULL AND EXISTS (
                 SELECT 1 FROM jsonb_array_elements(
                        CASE WHEN jsonb_typeof(ce.metadata -> 'attendees') = 'array'
                             THEN ce.metadata -> 'attendees' ELSE '[]'::jsonb END) att
                  WHERE att ->> 'self' = 'true'
                    AND COALESCE(att ->> 'response_status', att ->> 'responseStatus') = 'declined'
            ) THEN 'declined'
            WHEN a.ended_at IS NOT NULL THEN 'ended'
            WHEN GREATEST(a.agreed_starts_at, a.current_starts_at) <= now() THEN 'started'
            ELSE 'active' END AS state
  FROM calendar_owner_agreements a
  JOIN calendar_events ce ON ce.id = a.event_id
 WHERE ce.origin_ref IS NOT NULL
   AND NOT {quarantined_invitation_sql("ce")}
   AND GREATEST(a.agreed_starts_at, a.current_starts_at) > now() - interval '14 days'
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
    synced_at: datetime | None = None
    state: str = "active"
    title: str | None = None


def material_reasons(item: Agreed, head: Head | None) -> tuple[list[str], int]:
    """The material changes since acceptance, and how many minutes the start moved.

    Empty when the event is cancelled in the journal (the agreement is over, not changed). A
    journal head observed after the projection last wrote overrides the projected times.
    """
    start, end = item.current_start, item.current_end
    if head is not None:
        if head.status == "cancelled":
            return [], 0
        # The journal is newer than the projection when it was observed after the source's last
        # successful sync (the cursor); current_observed_at only means "last change".
        projected_at = item.synced_at or item.current_observed_at
        if head.observed_at > projected_at and head.starts_at is not None:
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


def _copy_rank(item: Agreed) -> tuple[float, str]:
    """Newest observation first, then the butler name: the freshest copy is the one reported."""
    return (-item.current_observed_at.timestamp(), item.butler)


def _freshest(items: Iterable[Agreed]) -> dict[tuple[str, datetime], Agreed]:
    """One copy per provider occurrence across schemas (active copies win over ended ones)."""
    chosen: dict[tuple[str, datetime], Agreed] = {}
    for item in sorted(items, key=lambda i: (i.state != "active", *_copy_rank(i))):
        chosen.setdefault((item.origin_ref, item.occurrence_start), item)
    return chosen


def resolution_causes(items: Iterable[Agreed], heads: Mapping[str, Head]) -> dict[str, str]:
    """Why each unchanged occurrence's condition would resolve: ``reverted``, ``declined``,
    ``cancelled``, ``started`` or ``ended`` (the seat or the source was lost). Only ``reverted``
    makes a delivered line wrong.
    """
    causes: dict[str, str] = {}
    for (origin_ref, occurrence_start), item in _freshest(items).items():
        head = heads.get(origin_ref)
        if item.state != "active":
            cause = item.state
        elif head is not None and head.status == "cancelled":
            cause = "cancelled"
        elif not material_reasons(item, head)[0]:
            cause = "reverted"
        else:
            continue
        causes[fingerprint(origin_ref, occurrence_start)] = cause
    return causes


def _changed(
    items: Iterable[Agreed], heads: Mapping[str, Head]
) -> tuple[list[tuple[Agreed, list[str], int]], int]:
    """Changed active occurrences (copies across schemas collapsed) and how many were checked."""
    chosen = _freshest(items)
    changed: list[tuple[Agreed, list[str], int]] = []
    checked = 0
    for item in chosen.values():
        if item.state != "active":
            continue
        checked += 1
        reasons, moved_minutes = material_reasons(item, heads.get(item.origin_ref))
        if reasons:
            changed.append((item, reasons, moved_minutes))
    return changed, checked


def build_observations(
    items: Iterable[Agreed], heads: Mapping[str, Head]
) -> tuple[list[Observation], int]:
    """One title-free observation per changed occurrence, copies across schemas collapsed.

    Returns the observations and how many accepted occurrences were checked.
    """
    changed, checked = _changed(items, heads)
    observations = [
        Observation(
            fingerprint=fingerprint(item.origin_ref, item.occurrence_start),
            identity_version=IDENTITY_VERSION,
            summary="An event you accepted changed: "
            + ", ".join(r.replace("_", " ") for r in reasons),
            metadata={
                "butler": item.butler,
                "event_id": item.event_id,
                "original_start": item.occurrence_start.isoformat(),
                "agreed_start": item.agreed_start.isoformat(),
                "reasons": reasons,
                "moved_minutes": moved_minutes,
            },
        )
        for item, reasons, moved_minutes in changed
    ]
    return observations, checked


def change_line(item: Agreed, reasons: list[str], moved_minutes: int) -> str:
    """The one owner-facing line. Third-party text passes only through the inert helpers."""
    from butlers.jobs.meeting_card import inert_text

    title = inert_text(item.title, 80) or "An event you accepted"
    what = [f"moved {moved_minutes} min" if r == "moved" else r.replace("_", " ") for r in reasons]
    return f"{title} changed since you accepted it: {', '.join(what)}."


def candidate_key(item: Agreed, fp: str, episode: int) -> str:
    """The broker dedup key of one condition episode: a reopened condition is a new candidate."""
    return f"{item.butler}:{CANDIDATE_CATEGORY}:{fp[:16]}:{episode}"


async def unproposed_episodes(
    pool: Any, items: Iterable[Agreed], heads: Mapping[str, Head], *, skip: set[str]
) -> list[Any]:
    """Active conditions whose candidate was never queued (a broker failure, a crash).

    The condition is durable but its candidate is a separate write, so on a complete tick every
    changed occurrence with an open episode and no candidate row under that episode's key is
    proposed again. ``skip`` holds the fingerprints this tick's transitions already cover.
    """
    from types import SimpleNamespace

    active = {
        row["fingerprint"]: row["episode"]
        for row in await pool.fetch(
            "SELECT fingerprint, episode FROM public.owner_conditions "
            "WHERE source = $1 AND state IN ('open', 'aging')",
            CONDITION_SOURCE,
        )
    }
    keys: dict[str, tuple[str, int]] = {}
    for item, _reasons, _moved in _changed(items, heads)[0]:
        fp = fingerprint(item.origin_ref, item.occurrence_start)
        if fp in active and fp not in skip:
            keys[candidate_key(item, fp, active[fp])] = (fp, active[fp])
    if not keys:
        return []
    have = {
        row["dedup_key"]
        for row in await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE dedup_key = ANY($1::text[])",
            list(keys),
        )
    }
    return [
        SimpleNamespace(fingerprint=fp, transition="opened", episode=episode)
        for key, (fp, episode) in keys.items()
        if key not in have
    ]


async def propose_changes(
    pool: Any,
    transitions: Iterable[Any],
    items: Iterable[Agreed],
    heads: Mapping[str, Head],
    *,
    now: datetime,
    broker: Any | None = None,
) -> int:
    """Propose one candidate per newly opened condition. Returns how many were queued.

    The premise is the condition itself, so a revert withdraws it unsent (or amends it in place
    after delivery). Timed lane when the occurrence starts within 24 hours, digest otherwise or
    while the timed lane is dark. Never raises: the condition is already durable.
    """
    fresh = {
        t.fingerprint: int(getattr(t, "episode", 1) or 1)
        for t in transitions
        if t.transition in ("opened", "reopened")
    }
    if not fresh:
        return 0
    if broker is None:
        import importlib

        broker = importlib.import_module("butlers.tools.switchboard.insight.broker")
    from butlers.core.insight_premise import owner_condition_premise

    queued = 0
    changed, _checked = _changed(items, heads)
    for item, reasons, moved_minutes in changed:
        fp = fingerprint(item.origin_ref, item.occurrence_start)
        if fp not in fresh:
            continue
        head = heads.get(item.origin_ref)
        start = head.starts_at if head and head.starts_at else item.current_start
        if start <= now:
            continue  # it has started: a change line about it is moot
        common: dict[str, Any] = {
            "origin_butler": item.butler,
            "priority": CANDIDATE_PRIORITY,
            "category": CANDIDATE_CATEGORY,
            "dedup_key": candidate_key(item, fp, fresh[fp]),
            "message": change_line(item, reasons, moved_minutes),
            "premise": owner_condition_premise(CONDITION_SOURCE, fp),
            "now": now,
        }
        try:
            result: dict[str, Any] = {}
            if now <= start <= now + TIMED_WITHIN:
                result = await broker.propose_insight_candidate(
                    pool,
                    expires_at=start,
                    lane="timed",
                    deliver_after=now,
                    **common,
                )
                if result.get("status") == "error":
                    result = {}  # timed lane dark or refused: digest instead
            if not result:
                result = await broker.propose_insight_candidate(pool, expires_at=start, **common)
            if result.get("status") == "accepted":
                queued += 1
        except Exception:
            logger.warning("calendar agreement drift: candidate not queued", exc_info=True)
    return queued


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
        synced_at=row.get("synced_at"),
        state=row.get("state") or "active",
        title=row.get("title"),
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
    causes = resolution_causes(items, heads)

    async def _stamp_cause(conn: Any, transitions: list[Any]) -> None:
        for t in transitions:
            if t.transition == "resolved":
                await conn.execute(
                    "UPDATE public.owner_conditions SET metadata = COALESCE(metadata, '{}'::jsonb) "
                    "|| jsonb_build_object('resolution_cause', $2::text) WHERE id = $1",
                    t.condition_id,
                    causes.get(t.fingerprint, "ended"),
                )

    try:
        transitions = await reconcile_snapshot(
            pool,
            source=CONDITION_SOURCE,
            observations=observations,
            snapshot_complete=complete,
            initial_grace_seconds=INITIAL_GRACE_S,
            post_write=_stamp_cause,
            # Only a revert makes a delivered line wrong; the others resolve with no amendment.
            amend_resolved=lambda t: causes.get(t.fingerprint) == "reverted",
        )
    except Exception:
        logger.warning("calendar agreement drift: reconciliation failed", exc_info=True)
        return _skip("reconcile_failed")
    recovered: list[Any] = []
    if complete:
        try:
            recovered = await unproposed_episodes(
                pool,
                items,
                heads,
                skip={t.fingerprint for t in transitions if t.transition in ("opened", "reopened")},
            )
        except Exception:
            logger.warning("calendar agreement drift: unproposed read failed", exc_info=True)
    queued = await propose_changes(
        pool, [*transitions, *recovered], items, heads, now=datetime.now(UTC)
    )
    return {
        "reconciled": True,
        "complete": complete,
        "checked": checked,
        "observed": len(observations),
        "queued": queued,
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
