"""The encounter ledger reducer (bu-5ii4gj.3 E2a).

Recomputes ``relationship.encounters`` from three inputs: the occurrence's participant records,
the event's own status and start, and the owner's debrief answer. The result is a pure function of
those inputs, so a replay converges and nothing here is a source of truth.

Scheduled hourly in ``roster/relationship/butler.toml``. ``calendar_prep`` and the meeting card
read the ledger for "last met"; the other readers (``interaction_sync`` and the Dunbar and
stale-contact weights) switch to it in later slices.

Precedence is owner testimony, then the event's own RSVP and status, then the clock:

1. debrief ``did_not_happen`` or ``moved`` -> that state, for every person
2. the guest declined -> ``declined_by_them``
3. debrief answered (``none_agreed`` or ``captured``) -> ``happened_attested``
4. the event is cancelled -> ``cancelled``
5. the owner declined -> ``declined_by_owner``
6. the occurrence has not started -> ``scheduled``
7. otherwise -> ``unknown``: an ended meeting nobody vouches for is never "met"

A person is only recorded when the participant record names them: an exact or manual resolution,
not a room, an active posture. An ambiguous or unresolved address is never a person here. Reads
go through the same participant seam as the E1 reader (``unavailable_event_ids`` and the current
resolver version), so an event whose participants are not ready leaves its prior rows alone.

A read that fails leaves every prior state unchanged: all reads finish before the one write
transaction opens, and the result says ``complete: False`` so a caller never advances a checkpoint
past a gap. Evidence references carry ids only, never an address or a name.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

import asyncpg

from butlers.core.identity.attendees import RESOLVER_VERSION
from butlers.core.temporal.calendar_source_roles import asserts_owner_time
from butlers.core.temporal.invitation_quarantine import quarantined_invitation_sql
from butlers.modules.calendar.participants import unavailable_event_ids

logger = logging.getLogger(__name__)

__all__ = ["Occurrence", "reduce_state", "run_encounter_reducer"]

#: How far back and ahead of ``now`` an occurrence's start may be to be recomputed.
LOOKBACK: Final = timedelta(days=90)
LOOKAHEAD: Final = timedelta(days=14)
#: One run recomputes at most this many occurrences, newest first.
MAX_OCCURRENCES: Final = 2000

_ANSWERED_HAPPENED: Final = frozenset({"none_agreed", "captured"})
#: A debrief the owner has not answered (yet, or ever). Any other state is the owner's word, so a
#: copy holding one is never dropped in a collapse (``did_not_happen`` and ``moved`` included).
_UNANSWERED: Final = frozenset({"pending", "expired"})

_OCCURRENCES_SQL: Final = f"""
    SELECT r.event_id, r.starts_at, r.cancelled, r.time_role, r.metadata, r.origin_ref
    FROM (
    SELECT o.*, dense_rank() OVER (
        ORDER BY abs(extract(epoch FROM (o.starts_at - $5::timestamptz))), o.starts_at,
                 COALESCE(o.origin_ref, o.event_id::text)
    ) AS grp
    FROM (
        SELECT ce.id AS event_id, ce.starts_at AS starts_at,
               (ce.status = 'cancelled') AS cancelled,
               cs.time_role AS time_role, ce.metadata AS metadata, ce.origin_ref AS origin_ref
        FROM calendar_events ce
        LEFT JOIN calendar_sources cs ON cs.id = ce.source_id
        WHERE ce.recurrence_rule IS NULL
          AND ce.metadata ->> 'source_type' = 'provider_event'
          AND ce.all_day = false
          AND ce.event_type NOT IN ('outOfOffice', 'workingLocation')
          AND NOT {quarantined_invitation_sql("ce")}
          AND ce.starts_at >= $1 AND ce.starts_at <= $2
          AND ($4::uuid[] IS NULL OR ce.id = ANY($4::uuid[]))
        UNION ALL
        SELECT ce.id, ci.starts_at, (ce.status = 'cancelled' OR ci.status = 'cancelled'),
               cs.time_role, ce.metadata, ce.origin_ref
        FROM calendar_event_instances ci
        JOIN calendar_events ce ON ce.id = ci.event_id
        LEFT JOIN calendar_sources cs ON cs.id = ce.source_id
        WHERE ce.recurrence_rule IS NOT NULL
          AND ce.metadata ->> 'source_type' = 'provider_event'
          AND ce.all_day = false
          AND ce.event_type NOT IN ('outOfOffice', 'workingLocation')
          AND NOT {quarantined_invitation_sql("ce")}
          AND ci.starts_at >= $1 AND ci.starts_at <= $2
          AND ($4::uuid[] IS NULL OR ce.id = ANY($4::uuid[]))
    ) o
    ) r
    -- The cap counts meetings, not rows: every copy of one meeting shares a rank, so the cap
    -- can never split copies and let a collapse keep a different one on the next run.
    WHERE ($3::int IS NULL OR r.grp <= $3)
    ORDER BY r.grp, r.event_id
"""

#: The ledger's own occurrences inside the window, to find the ones the calendar no longer has.
_LEDGER_KEYS_SQL: Final = """
    SELECT DISTINCT event_id, occurrence_start
    FROM encounters
    WHERE occurrence_start >= $1 AND occurrence_start <= $2
"""

_PARTICIPANTS_SQL: Final = """
    SELECT p.event_id, p.resolution, p.role, p.response_status, p.entity_id,
           COALESCE(pe.posture, 'active') AS posture
    FROM calendar_event_participants p
    LEFT JOIN public.entities pe ON pe.id = p.entity_id
    WHERE p.event_id = ANY($1::uuid[]) AND p.resolver_version = $2
"""

_DEBRIEFS_SQL: Final = """
    SELECT id, event_id, occurrence_start, state
    FROM meeting_debriefs
    WHERE event_id = ANY($1::uuid[])
"""


@dataclass(frozen=True, slots=True)
class Occurrence:
    event_id: uuid.UUID
    starts_at: datetime
    cancelled: bool


@dataclass(frozen=True, slots=True)
class _Inputs:
    occurrences: list[Occurrence]
    not_ready: set[uuid.UUID]
    participants: dict[uuid.UUID, list[Any]]
    debriefs: dict[Any, Any]
    ghosts: list[tuple[uuid.UUID, datetime]]


@dataclass(slots=True)
class _Plan:
    rows: dict[tuple[uuid.UUID, uuid.UUID, datetime], tuple[str, str, str, dict[str, str]]] = field(
        default_factory=dict
    )
    #: occurrences whose people were fully computed, so rows outside ``rows`` are stale
    settled: list[Occurrence] = field(default_factory=list)
    #: ledger occurrences the calendar no longer has (a reschedule, a deleted event, a dropped
    #: instance), whose participants were readable
    ghosts: list[tuple[uuid.UUID, datetime]] = field(default_factory=list)


def reduce_state(
    *,
    debrief_state: str | None,
    cancelled: bool,
    owner_declined: bool,
    guest_declined: bool,
    started: bool,
) -> tuple[str, str]:
    """Pure: the ``(state, basis)`` of one person at one occurrence."""
    if debrief_state in ("did_not_happen", "moved"):
        return debrief_state, "owner_debrief"
    if guest_declined:
        return "declined_by_them", "rsvp"
    if debrief_state in _ANSWERED_HAPPENED:
        return "happened_attested", "owner_debrief"
    if cancelled:
        return "cancelled", "schedule"
    if owner_declined:
        return "declined_by_owner", "rsvp"
    return ("unknown" if started else "scheduled"), "schedule"


def _people(rows: Sequence[Mapping[str, Any]]) -> tuple[bool, dict[uuid.UUID, bool]]:
    """The owner's decline and, per recordable guest, whether that guest declined."""
    owner_declined = any(
        r["resolution"] == "owner" and (r["response_status"] or "").lower() == "declined"
        for r in rows
    )
    guests: dict[uuid.UUID, bool] = {}
    for r in rows:
        if (
            r["resolution"] in ("exact", "manual")
            and r["role"] != "resource"
            and r["entity_id"] is not None
            and r["posture"] == "active"
        ):
            declined = (r["response_status"] or "").lower() == "declined"
            guests[r["entity_id"]] = guests.get(r["entity_id"], True) and declined
    return owner_declined, guests


def _plan(
    occurrences: Sequence[Occurrence],
    not_ready: set[uuid.UUID],
    participants: Mapping[uuid.UUID, list[Mapping[str, Any]]],
    debriefs: Mapping[tuple[uuid.UUID, datetime], tuple[uuid.UUID, str]],
    now: datetime,
) -> _Plan:
    plan = _Plan()
    for occ in occurrences:
        if occ.event_id in not_ready:
            continue
        owner_declined, guests = _people(participants.get(occ.event_id, []))
        debrief = debriefs.get((occ.event_id, occ.starts_at))
        refs = {"debrief_id": str(debrief[0])} if debrief else {}
        for entity_id, guest_declined in guests.items():
            state, basis = reduce_state(
                debrief_state=debrief[1] if debrief else None,
                cancelled=occ.cancelled,
                owner_declined=owner_declined,
                guest_declined=guest_declined,
                started=occ.starts_at <= now,
            )
            used = refs if basis == "owner_debrief" else {}
            plan.rows[(entity_id, occ.event_id, occ.starts_at)] = (
                str(occ.event_id),
                state,
                basis,
                used,
            )
        plan.settled.append(occ)
    return plan


def _collapse_copies(
    rows: list[Any],
    answered: set[tuple[uuid.UUID, datetime]] = frozenset(),  # type: ignore[assignment]
) -> tuple[list[Any], set[tuple[uuid.UUID, datetime]]]:
    """One row per meeting: copies of the same provider event on several calendars collapse.

    A meeting on the owner's calendar and the same invite on a partner or shared calendar (the
    owner's seat accepted on both) are two ``calendar_events`` rows with the same provider event
    id and start. Only one may become an encounter: the copy holding an answered debrief (any
    state but pending or expired; never drop the owner's word), else the owner-role source's copy,
    else the smallest id. Returns the kept rows and the ``(event_id, starts_at)`` keys of the
    copies dropped, so a ledger row already written for a dropped copy is retired, not kept.
    """
    groups: dict[tuple[str, datetime], list[Any]] = {}
    kept: list[Any] = []
    for row in rows:
        if not row["origin_ref"]:
            kept.append(row)
            continue
        groups.setdefault((row["origin_ref"], row["starts_at"]), []).append(row)
    dropped: set[tuple[uuid.UUID, datetime]] = set()
    for copies in groups.values():
        copies.sort(
            key=lambda r: (
                (r["event_id"], r["starts_at"]) not in answered,
                r["time_role"] != "owner",
                str(r["event_id"]),
            )
        )
        kept.append(copies[0])
        dropped.update((r["event_id"], r["starts_at"]) for r in copies[1:])
    return kept, dropped


async def _read_inputs(pool: asyncpg.Pool, now: datetime) -> _Inputs:
    lo, hi = now - LOOKBACK, now + LOOKAHEAD
    found = await pool.fetch(_OCCURRENCES_SQL, lo, hi, MAX_OCCURRENCES, None, now)
    # Only the owner's own time is an encounter: an event on a partner or shared calendar is not,
    # even when the owner's row there reads "accepted". The role gate only: the owner's decline is
    # a state of its own here (declined_by_owner), not a reason to drop the occurrence.
    found = [r for r in found if asserts_owner_time(r["time_role"], r["metadata"])]
    answered = {
        (r["event_id"], r["occurrence_start"])
        for r in await pool.fetch(_DEBRIEFS_SQL, sorted({r["event_id"] for r in found}, key=str))
        if r["state"] not in _UNANSWERED
    }
    found, dropped_copies = _collapse_copies(found, answered)
    occurrences = [Occurrence(r["event_id"], r["starts_at"], bool(r["cancelled"])) for r in found]
    seen = {(o.event_id, o.starts_at) for o in occurrences}
    # A ledger row the capped read did not return is checked against the calendar itself, per
    # event and uncapped, so the cap can never make valid history look like a ghost.
    missing = [
        (r["event_id"], r["occurrence_start"])
        for r in await pool.fetch(_LEDGER_KEYS_SQL, lo, hi)
        if (r["event_id"], r["occurrence_start"]) not in seen
    ]
    ghosts: list[tuple[uuid.UUID, datetime]] = []
    if missing:
        ledger_events = sorted({e for e, _ in missing}, key=str)
        current = {
            (r["event_id"], r["starts_at"])
            for r in await pool.fetch(_OCCURRENCES_SQL, lo, hi, None, ledger_events, now)
            if asserts_owner_time(r["time_role"], r["metadata"])
        }
        ghosts = [key for key in missing if key not in current or key in dropped_copies]
    event_ids = sorted({o.event_id for o in occurrences} | {e for e, _ in ghosts}, key=str)
    if not event_ids:
        return _Inputs(occurrences, set(), {}, {}, [])
    not_ready = await unavailable_event_ids(pool, event_ids)
    by_event: dict[uuid.UUID, list[Any]] = {}
    for r in await pool.fetch(_PARTICIPANTS_SQL, event_ids, RESOLVER_VERSION):
        by_event.setdefault(r["event_id"], []).append(r)
    debriefs = {
        (r["event_id"], r["occurrence_start"]): (r["id"], r["state"])
        for r in await pool.fetch(_DEBRIEFS_SQL, event_ids)
    }
    return _Inputs(
        occurrences,
        not_ready,
        by_event,
        debriefs,
        [key for key in ghosts if key[0] not in not_ready],
    )


async def run_encounter_reducer(
    pool: asyncpg.Pool, *, now: datetime | None = None
) -> dict[str, Any]:
    """Recompute the ledger for the recent occurrences. Never raises for an unreadable input.

    Returns ``{"complete": bool, "occurrences": n, "upserted": n, "removed": n, "skipped": n}``.
    ``complete`` is False when a read failed; nothing was written then.
    """
    now_utc: datetime = now or await pool.fetchval("SELECT now()")
    try:
        inputs = await _read_inputs(pool, now_utc)
    except Exception as exc:  # noqa: BLE001 - a failed read must leave prior states unchanged
        logger.warning("encounter_ledger_read_failed error=%s", type(exc).__name__)
        return {"complete": False, "occurrences": 0, "upserted": 0, "removed": 0, "skipped": 0}

    occurrences = inputs.occurrences
    plan = _plan(occurrences, inputs.not_ready, inputs.participants, inputs.debriefs, now_utc)
    plan.ghosts = inputs.ghosts
    removed = 0
    async with pool.acquire() as conn, conn.transaction():
        for (entity_id, event_id, starts_at), (series, state, basis, refs) in plan.rows.items():
            await conn.execute(
                """
                INSERT INTO encounters
                    (entity_id, event_id, occurrence_start, series_key, state, basis,
                     evidence_refs, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::text::jsonb, now())
                ON CONFLICT (entity_id, event_id, occurrence_start) DO UPDATE
                SET series_key = EXCLUDED.series_key, state = EXCLUDED.state,
                    basis = EXCLUDED.basis, evidence_refs = EXCLUDED.evidence_refs,
                    updated_at = now()
                WHERE (encounters.series_key, encounters.state, encounters.basis,
                       encounters.evidence_refs)
                      IS DISTINCT FROM (EXCLUDED.series_key, EXCLUDED.state, EXCLUDED.basis,
                                        EXCLUDED.evidence_refs)
                """,
                entity_id,
                event_id,
                starts_at,
                series,
                state,
                basis,
                json.dumps(refs),
            )
        for occ in plan.settled:
            keep = [e for (e, ev, st) in plan.rows if ev == occ.event_id and st == occ.starts_at]
            result = await conn.execute(
                """
                DELETE FROM encounters
                WHERE event_id = $1 AND occurrence_start = $2 AND NOT (entity_id = ANY($3::uuid[]))
                """,
                occ.event_id,
                occ.starts_at,
                keep,
            )
            removed += int(result.rsplit(" ", 1)[-1])
        for event_id, starts_at in plan.ghosts:
            result = await conn.execute(
                "DELETE FROM encounters WHERE event_id = $1 AND occurrence_start = $2",
                event_id,
                starts_at,
            )
            removed += int(result.rsplit(" ", 1)[-1])
    return {
        "complete": True,
        "occurrences": len(plan.settled),
        "upserted": len(plan.rows),
        "removed": removed,
        "skipped": len(occurrences) - len(plan.settled),
    }
