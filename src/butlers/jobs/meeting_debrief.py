"""Meeting debrief job: every ended meeting with other people gets asked about once.

Deterministic, **zero-LLM** Relationship job (bu-q7vx1q.12). Most promises are made in
meetings, but commitment extraction only runs on routed conversations, so "I'll send you
the deck" said in a 1:1 never reaches the ledger. This job closes that gap from the
asking side: it records one ``relationship.meeting_debriefs`` row per ended calendar
occurrence the owner attended with other people, then proposes one batched end-of-day
insight listing them. The owner's reply is recorded by the ``meeting_debrief_answer``
tool (``roster/relationship/tools/meeting_debrief.py``); nothing is created without it.

Selection (REQ-butler-relationship "Meeting debrief job")
---------------------------------------------------------
An occurrence is debriefed when it has ended inside the lookback window, is not cancelled,
not all-day, not butler-generated, not transparent (free time), the owner did not decline
it, and at least one *other* person remains after removing the owner's own entry, room
resources, declined guests and anyone whose posture is not ``active``. Recurring series
are debriefed per occurrence: the unique key is ``(event_id, occurrence_start)``.

Posture is checked twice, at selection and again at prompt time, because the owner can
mark someone memorial or no_contact between the two. A person who is not ``active`` is
never named in a prompt, and a meeting left with no askable attendee is never prompted.
An attendee with no resolved entity is kept and listed by email: a stranger is still
someone the owner may have promised something to.

Back-off
--------
Three consecutive prompt batches without any answer drop the cadence to one batch per
seven days, and the batch that crosses the threshold says so. Any answer restores the
daily cadence. The count is derived from the table (distinct ``prompted_at`` since the
latest ``answered_at``), not stored, so it cannot drift from the rows it describes.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.temporal.calendar_provenance import (
    is_explicit_butler_generated,
    is_owner_attending,
    is_transparent,
)
from butlers.jobs.briefing import SGT

logger = logging.getLogger(__name__)

__all__ = [
    "BACKOFF_CADENCE",
    "BACKOFF_THRESHOLD",
    "DEBRIEF_CATEGORY",
    "EXPIRY_AFTER",
    "LOOKBACK",
    "active_entity_ids",
    "decode_attendees",
    "run_meeting_debrief",
]

#: How far back an ended occurrence is still eligible for a debrief row.
LOOKBACK = timedelta(days=2)

#: A debrief unanswered this long after the meeting ended is expired, never re-asked.
#: Longer than ``BACKOFF_CADENCE`` so a weekly batch can still list a week-old meeting.
EXPIRY_AFTER = timedelta(days=8)

#: Consecutive unanswered prompt batches before the cadence drops.
BACKOFF_THRESHOLD = 3

#: Minimum gap between prompt batches once the owner has disengaged.
BACKOFF_CADENCE = timedelta(days=7)

DEBRIEF_CATEGORY = "meeting-debrief"

_INSIGHT_PRIORITY = 40
_INSIGHT_TTL = timedelta(hours=24)

InsightProposer = Callable[..., Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

_OCCURRENCE_SQL = """
    SELECT ce.id AS event_id, ce.title AS title, ce.starts_at AS starts_at,
           ce.ends_at AS ends_at, ce.metadata AS metadata
    FROM calendar_events ce
    WHERE ce.recurrence_rule IS NULL
      AND ce.status <> 'cancelled'
      AND ce.all_day = false
      AND ce.ends_at <= $2
      AND ce.ends_at > $1
    UNION ALL
    SELECT ce.id, ce.title, ci.starts_at, ci.ends_at, ce.metadata || ci.metadata
    FROM calendar_event_instances ci
    JOIN calendar_events ce ON ce.id = ci.event_id
    WHERE ce.recurrence_rule IS NOT NULL
      AND ce.status <> 'cancelled'
      AND ci.status <> 'cancelled'
      AND ce.all_day = false
      AND ci.ends_at <= $2
      AND ci.ends_at > $1
    ORDER BY ends_at
"""


def _decode_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw if isinstance(raw, dict) else {}


def decode_attendees(raw: Any) -> list[dict[str, Any]]:
    """Decode the JSONB snapshot whether or not the pool registers a jsonb codec."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw if isinstance(raw, list) else []


def _other_attendees(meta: dict[str, Any]) -> list[dict[str, str]]:
    """The people besides the owner who were expected at the meeting, keyed by email."""
    attendees = meta.get("attendees")
    if not isinstance(attendees, list):
        return []
    others: dict[str, dict[str, str]] = {}
    for att in attendees:
        if not isinstance(att, dict) or att.get("self") is True or att.get("resource") is True:
            continue
        status = att.get("response_status", att.get("responseStatus"))
        if isinstance(status, str) and status.strip().lower() == "declined":
            continue
        email = att.get("email")
        if not isinstance(email, str) or not email.strip():
            continue
        email = email.strip().lower()
        display = att.get("display_name", att.get("displayName"))
        others[email] = {
            "email": email,
            "display_name": display if isinstance(display, str) else "",
        }
    return list(others.values())


async def _resolve_people(pool: asyncpg.Pool, emails: set[str]) -> dict[str, dict[str, Any]]:
    """Map email -> {entity_id, name, posture, is_owner} for emails that resolve."""
    if not emails:
        return {}
    rows = await pool.fetch(
        """
        SELECT ef.subject                AS entity_id,
               LOWER(ef.object)          AS email,
               e.canonical_name          AS name,
               e.posture                 AS posture,
               COALESCE(e.roles, '{}')   AS roles
        FROM relationship.entity_facts ef
        JOIN public.entities e ON e.id = ef.subject
        WHERE ef.predicate   = 'has-email'
          AND ef.object_kind = 'literal'
          AND ef.validity    = 'active'
          AND LOWER(ef.object) = ANY($1::text[])
        """,
        sorted(emails),
    )
    return {
        row["email"]: {
            "entity_id": str(row["entity_id"]),
            "name": row["name"] or "",
            "posture": row["posture"],
            "is_owner": "owner" in list(row["roles"] or []),
        }
        for row in rows
    }


async def _record_new_debriefs(pool: asyncpg.Pool, now: datetime) -> tuple[int, int]:
    """Insert a debrief row per qualifying occurrence. Returns (considered, inserted)."""
    rows = await pool.fetch(_OCCURRENCE_SQL, now - LOOKBACK, now)

    staged: list[tuple[Any, dict[str, Any], list[dict[str, str]]]] = []
    emails: set[str] = set()
    for row in rows:
        meta = _decode_metadata(row["metadata"])
        if (
            is_explicit_butler_generated(meta)
            or is_transparent(meta)
            or not is_owner_attending(meta)
        ):
            continue
        others = _other_attendees(meta)
        if not others:
            continue
        staged.append((row, meta, others))
        emails.update(a["email"] for a in others)

    people = await _resolve_people(pool, emails)

    inserted = 0
    for row, _meta, others in staged:
        snapshot: list[dict[str, Any]] = []
        for att in others:
            person = people.get(att["email"])
            if person is None:
                snapshot.append(
                    {"entity_id": None, "name": att["display_name"], "email": att["email"]}
                )
            elif person["is_owner"] or person["posture"] != "active":
                continue
            else:
                snapshot.append(
                    {
                        "entity_id": person["entity_id"],
                        "name": person["name"],
                        "email": att["email"],
                    }
                )
        if not snapshot:
            continue
        result = await pool.fetchval(
            """
            INSERT INTO meeting_debriefs
                (event_id, occurrence_start, occurrence_end, event_title, attendees)
            VALUES ($1, $2, $3, $4, $5::text::jsonb)
            ON CONFLICT (event_id, occurrence_start) DO NOTHING
            RETURNING id
            """,
            row["event_id"],
            row["starts_at"],
            row["ends_at"],
            row["title"] or "",
            json.dumps(snapshot),
        )
        if result is not None:
            inserted += 1
    return len(staged), inserted


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _label(att: dict[str, Any]) -> str:
    return att.get("name") or att.get("email") or "someone"


def _compose_message(items: list[tuple[str, list[dict[str, Any]]]], *, weekly_notice: bool) -> str:
    noun = "meeting" if len(items) == 1 else "meetings"
    lines = [f"You had {len(items)} {noun} with other people. Anything agreed?"]
    for position, (title, attendees) in enumerate(items, start=1):
        who = ", ".join(_label(a) for a in attendees)
        lines.append(f"{position}. {title or 'Untitled'} ({who})")
    lines.append(
        'Reply with the number and what was agreed ("1: send Sam the deck by Fri"), '
        'or "1: none". Replying is optional.'
    )
    if weekly_notice:
        lines.append("You have not replied to the last few of these, so I will ask weekly.")
    return "\n".join(lines)


async def _backoff_state(pool: asyncpg.Pool) -> tuple[int, datetime | None]:
    """Consecutive unanswered prompt batches and the last batch instant."""
    row = await pool.fetchrow(
        """
        SELECT COUNT(DISTINCT prompted_at) AS unanswered, MAX(prompted_at) AS last_prompted
        FROM meeting_debriefs
        WHERE prompted_at IS NOT NULL
          AND state IN ('pending', 'expired')
          AND prompted_at > COALESCE(
                (SELECT MAX(answered_at) FROM meeting_debriefs), '-infinity'::timestamptz)
        """
    )
    return int(row["unanswered"] or 0), row["last_prompted"]


async def active_entity_ids(pool: asyncpg.Pool, entity_ids: set[str]) -> set[str]:
    """The subset of *entity_ids* whose posture is ``active`` (all others are never named)."""
    if not entity_ids:
        return set()
    rows = await pool.fetch(
        """
        SELECT id FROM public.entities
        WHERE id = ANY($1::uuid[]) AND posture = 'active'
        """,
        [uuid.UUID(e) for e in entity_ids],
    )
    return {str(r["id"]) for r in rows}


async def _propose_batch(
    pool: asyncpg.Pool, insight_proposer: InsightProposer, now: datetime
) -> dict[str, Any]:
    unanswered, last_prompted = await _backoff_state(pool)
    if (
        unanswered >= BACKOFF_THRESHOLD
        and last_prompted is not None
        and now - last_prompted < BACKOFF_CADENCE
    ):
        return {"prompted": 0, "skipped": "backoff"}

    rows = await pool.fetch(
        """
        SELECT id, event_title, attendees
        FROM meeting_debriefs
        WHERE state = 'pending' AND prompted_at IS NULL
        ORDER BY occurrence_end, id
        """
    )
    decoded = [(r["id"], r["event_title"], decode_attendees(r["attendees"])) for r in rows]
    active = await active_entity_ids(
        pool, {a["entity_id"] for _, _, atts in decoded for a in atts if a["entity_id"]}
    )

    askable: list[tuple[Any, str, list[dict[str, Any]]]] = []
    unaskable: list[Any] = []
    for debrief_id, title, atts in decoded:
        kept = [a for a in atts if a["entity_id"] is None or a["entity_id"] in active]
        (askable if kept else unaskable).append((debrief_id, title, kept))  # type: ignore[arg-type]
    if unaskable:
        await pool.execute(
            "UPDATE meeting_debriefs SET state = 'expired' WHERE id = ANY($1::uuid[])",
            [d[0] for d in unaskable],
        )
    if not askable:
        return {"prompted": 0, "skipped": "nothing_to_ask"}

    message = _compose_message(
        [(title, atts) for _, title, atts in askable],
        weekly_notice=unanswered + 1 == BACKOFF_THRESHOLD,
    )
    try:
        result = await insight_proposer(
            pool,
            origin_butler="relationship",
            priority=_INSIGHT_PRIORITY,
            category=DEBRIEF_CATEGORY,
            dedup_key=f"relationship:meeting-debrief:{now.astimezone(SGT).date().isoformat()}",
            message=message,
            expires_at=now + _INSIGHT_TTL,
            cooldown_days=1,
            metadata=json.dumps({"debrief_ids": [str(d[0]) for d in askable]}),
            now=now,
        )
    except Exception:
        logger.exception("meeting_debrief: proposing the debrief batch raised")
        return {"prompted": 0, "skipped": "error"}

    status = (result or {}).get("status", "error")
    if status != "accepted":
        # error: retry on the next run; filtered: insights are off, the rows age out.
        return {"prompted": 0, "skipped": status}

    async with pool.acquire() as conn, conn.transaction():
        for position, (debrief_id, _title, _atts) in enumerate(askable, start=1):
            await conn.execute(
                """
                UPDATE meeting_debriefs
                SET prompted_at = $2, batch_position = $3
                WHERE id = $1
                """,
                debrief_id,
                now,
                position,
            )
    return {"prompted": len(askable)}


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


async def run_meeting_debrief(
    pool: asyncpg.Pool,
    *,
    insight_proposer: InsightProposer,
) -> dict[str, Any]:
    """Run one debrief tick: record new debriefs, expire stale ones, prompt once.

    Reads its own clock once from Postgres. Never raises for a bad event: an
    unreadable calendar projection degrades to recording nothing this run.
    """
    now: datetime = await pool.fetchval("SELECT now()")

    considered = inserted = 0
    try:
        considered, inserted = await _record_new_debriefs(pool, now)
    except asyncpg.exceptions.UndefinedTableError:
        logger.info("meeting_debrief: calendar projection unavailable; recording nothing")

    expired = await pool.fetchval(
        """
        WITH gone AS (
            UPDATE meeting_debriefs SET state = 'expired'
            WHERE state = 'pending' AND occurrence_end < $1
            RETURNING 1
        ) SELECT COUNT(*) FROM gone
        """,
        now - EXPIRY_AFTER,
    )
    prompt = await _propose_batch(pool, insight_proposer, now)
    result = {
        "butler": "relationship",
        "considered": considered,
        "recorded": inserted,
        "expired": int(expired or 0),
        **prompt,
    }
    logger.info("meeting_debrief: %s", result)
    return result
