"""Calendar meeting-prep contribution job.

Deterministic, **zero-LLM** scheduled job that precomputes per-event meeting-prep
context for the calendar workspace's prep-rail read endpoint
(``GET /api/calendar/workspace/prep/{event_id}``).

Per RFC-0020 the prep rail MUST read from a precomputed contribution-job path
(cached ``state``), NOT a direct cross-schema SELECT against ``relationship.*`` /
``health.*`` / email and NOT a per-open LLM session. The calendar workspace fans
out across ALL calendar-owning butlers, most of which (general/finance/health/
relationship/lifestyle) have no email module — so the prep context has to be
precomputed by the butler that *owns* the data (here: relationship, which owns
the entity graph, co-attended edges and relationship notes) and merged at read
time through the cross-schema view ``calendar.v_prep_contributions`` (migration
``core_142``).

This job runs inside the relationship butler's schema (it may read its own
``relationship.*`` / ``public.entities`` tables — that is the deterministic
precompute, not the request-time read), and writes one prep envelope per
entity-linked upcoming event under the key ``calendar/prep/<event_id>``:

    {
        "butler":     "relationship",
        "event_id":   "<uuid>",
        "event_title": "...",
        "event_starts_at": "ISO-8601",
        "has_context": <bool>,            # at least one resolvable attendee
        "attendees": [
            {
                "entity_id":      "<uuid>",
                "name":           "...",
                "dunbar_tier":    <int|null>,   # letter-mark source (FE maps int->letter)
                "notes":          [{"kind": "...", "text": "..."}, ...],
                "last_met":       "ISO-8601|null",   # most recent prior co-attended event
                "last_met_event": "...|null",
                "message_context": [],          # populated by email-owning butlers (future)
            },
            ...
        ],
    }

This module REUSES the overlay contribution pattern (``butlers.jobs.calendar_overlay``):
the same ``state`` store and prune approach, registered in the **existing**
``_DETERMINISTIC_SCHEDULE_JOB_REGISTRY``. It is NOT a parallel scheduler.

Design reference: openspec/changes/calendar-prep-rail/
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import asyncpg

from butlers.core.state import state_delete, state_list, state_set
from butlers.jobs.briefing import SGT, today_sgt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: State key prefix bounding prep contributions away from overlay/briefing keys.
#: Mirrors ``calendar.v_prep_contributions``'s ``key LIKE 'calendar/prep/%'``
#: filter (RFC 0010 Guardrail #3).
PREP_KEY_PREFIX = "calendar/prep/"

#: How many days ahead (inclusive of today) the prep job precomputes context for.
#: A bounded rolling lookahead window so the cache stays small and forward-looking.
PREP_LOOKAHEAD_DAYS = 30

#: Memory-fact predicates treated as durable relationship "notes" surfaced on the
#: prep rail (CRM narrative facts, not volatile interaction logs).
NOTE_PREDICATES: tuple[str, ...] = ("contact_note", "meeting_note", "life_event")

#: Cap on the number of notes surfaced per attendee (highest-importance first).
MAX_NOTES_PER_ATTENDEE = 5

#: How far back the message-context job scans for recent email threads with an
#: attendee. Bounded so the scan stays cheap and the surfaced context is recent.
MESSAGE_CONTEXT_LOOKBACK_DAYS = 90

#: Cap on the number of recent threads surfaced per attendee (most-recent first).
MAX_THREADS_PER_ATTENDEE = 3

#: Max characters of a message body surfaced as a thread snippet.
SNIPPET_MAX_LEN = 200


# ---------------------------------------------------------------------------
# Envelope schema
# ---------------------------------------------------------------------------


class PrepNote(TypedDict):
    kind: str
    text: str


class PrepAttendee(TypedDict):
    entity_id: str
    name: str
    dunbar_tier: int | None
    notes: list[PrepNote]
    last_met: str | None
    last_met_event: str | None
    message_context: list[dict[str, Any]]


class PrepContribution(TypedDict):
    butler: str
    event_id: str
    event_title: str
    event_starts_at: str
    has_context: bool
    attendees: list[PrepAttendee]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def prep_key(event_id: str) -> str:
    """Return the state store key for a prep contribution for *event_id*."""
    return f"{PREP_KEY_PREFIX}{event_id}"


def _coerce_tier(raw: Any) -> int | None:
    """Best-effort parse of a ``dunbar_tier_override`` fact ``content`` into an int.

    The override fact stores its value either as a bare integer string ("5") or
    as a small JSON payload (``{"tier": 5}`` / ``5``). Anything unparseable
    degrades to ``None`` — the tier is a cosmetic letter-mark, never load-bearing.
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            pass
        try:
            parsed = json.loads(stripped)
        except (TypeError, json.JSONDecodeError):
            return None
        if isinstance(parsed, int):
            return parsed
        if isinstance(parsed, dict):
            val = parsed.get("tier")
            return val if isinstance(val, int) else None
    return None


async def prune_old_prep_contributions(pool: asyncpg.Pool, *, live_event_ids: set[str]) -> int:
    """Delete prep envelopes for events no longer in the lookahead window.

    Prep envelopes are keyed per-event (not per-date), so the prune deletes any
    ``calendar/prep/*`` key whose event id is not in the freshly-written
    ``live_event_ids`` set (past events that scrolled out of the window). A no-op
    when there is nothing stale.

    Returns the number of deleted entries.
    """
    keys: list[str] = await state_list(pool, prefix=PREP_KEY_PREFIX)  # type: ignore[assignment]
    pruned = 0
    for key in keys:
        event_id = key[len(PREP_KEY_PREFIX) :]
        if event_id not in live_event_ids:
            await state_delete(pool, key)
            pruned += 1
            logger.debug("Pruned stale prep contribution key: %s", key)
    return pruned


# ---------------------------------------------------------------------------
# Relationship prep contribution job
# ---------------------------------------------------------------------------


async def run_relationship_calendar_prep_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Relationship butler calendar meeting-prep contribution job (deterministic, zero LLM).

    For each entity-linked event in the rolling lookahead window, precompute the
    prep context — resolved attendees (name + Dunbar-tier letter-mark), durable
    relationship notes, and last-met from prior co-attended events — and write
    one envelope per event under ``calendar/prep/<event_id>``. Stale envelopes
    for events that scrolled out of the window are pruned. No LLM session is
    spawned.

    The job reads its OWN schema (``calendar_event_entities`` projection joined to
    ``calendar_events`` + ``public.entities`` + the memory ``facts`` store) — this
    is the sanctioned deterministic precompute. The request-time prep-rail read
    never touches these tables; it reads only the cached ``calendar.v_prep_contributions``
    view.
    """
    del job_args

    today = today_sgt()
    today_str = today.isoformat()
    now_utc = datetime.now(tz=UTC)

    # SGT-midnight window boundaries converted to UTC for TIMESTAMPTZ comparison.
    sgt_midnight = datetime(today.year, today.month, today.day, tzinfo=SGT)
    window_start = sgt_midnight.astimezone(UTC)
    window_end = (sgt_midnight + timedelta(days=PREP_LOOKAHEAD_DAYS + 1)).astimezone(UTC)

    # --- Upcoming entity-linked events in the window ---
    event_rows = await pool.fetch(
        """
        SELECT ce.id AS event_id,
               ce.title AS title,
               ce.starts_at AS starts_at,
               array_agg(DISTINCT cee.entity_id) AS entity_ids
        FROM calendar_event_entities cee
        JOIN calendar_events ce ON ce.id = cee.event_id
        WHERE ce.starts_at >= $1
          AND ce.starts_at < $2
          AND COALESCE(ce.status, 'confirmed') <> 'cancelled'
        GROUP BY ce.id, ce.title, ce.starts_at
        ORDER BY ce.starts_at ASC
        """,
        window_start,
        window_end,
    )

    # Collect the full set of attendee entity ids so per-attendee lookups can be
    # batched into one query each (instead of N+1 per event).
    all_entity_ids: set[Any] = set()
    for row in event_rows:
        for eid in row["entity_ids"] or []:
            if eid is not None:
                all_entity_ids.add(eid)

    names_by_id: dict[Any, str] = {}
    tier_by_id: dict[Any, int | None] = {}
    notes_by_id: dict[Any, list[PrepNote]] = {}
    last_met_by_id: dict[Any, tuple[datetime, str | None]] = {}

    if all_entity_ids:
        entity_id_list = list(all_entity_ids)

        # Names from the shared entity registry.
        name_rows = await pool.fetch(
            """
            SELECT id, COALESCE(canonical_name, 'Unknown') AS name
            FROM public.entities
            WHERE id = ANY($1::uuid[])
            """,
            entity_id_list,
        )
        for row in name_rows:
            names_by_id[row["id"]] = row["name"]

        # Manual Dunbar-tier overrides (the only deterministic, cheap tier signal;
        # rank-based tiers require the full scoring pass and are intentionally not
        # recomputed here). Absent override → tier is None (FE shows no letter-mark).
        tier_rows = await pool.fetch(
            """
            SELECT entity_id, content
            FROM facts
            WHERE predicate = 'dunbar_tier_override'
              AND scope = 'relationship'
              AND validity = 'active'
              AND entity_id = ANY($1::uuid[])
            """,
            entity_id_list,
        )
        for row in tier_rows:
            tier_by_id[row["entity_id"]] = _coerce_tier(row["content"])

        # Durable relationship notes (highest-importance first), capped per entity.
        note_rows = await pool.fetch(
            """
            SELECT entity_id, predicate, content, importance,
                   COALESCE(valid_at, created_at) AS ts
            FROM facts
            WHERE predicate = ANY($2::text[])
              AND scope = 'relationship'
              AND validity = 'active'
              AND entity_id = ANY($1::uuid[])
              AND content <> ''
            ORDER BY entity_id, importance DESC, ts DESC
            """,
            entity_id_list,
            list(NOTE_PREDICATES),
        )
        for row in note_rows:
            bucket = notes_by_id.setdefault(row["entity_id"], [])
            if len(bucket) >= MAX_NOTES_PER_ATTENDEE:
                continue
            bucket.append({"kind": row["predicate"], "text": row["content"]})

        # Last-met: the most recent PAST event each attendee co-attended.
        last_met_rows = await pool.fetch(
            """
            SELECT DISTINCT ON (cee.entity_id)
                   cee.entity_id AS entity_id,
                   ce.starts_at AS starts_at,
                   ce.title AS title
            FROM calendar_event_entities cee
            JOIN calendar_events ce ON ce.id = cee.event_id
            WHERE cee.entity_id = ANY($1::uuid[])
              AND ce.starts_at < $2
              AND COALESCE(ce.status, 'confirmed') <> 'cancelled'
            ORDER BY cee.entity_id, ce.starts_at DESC
            """,
            entity_id_list,
            now_utc,
        )
        for row in last_met_rows:
            last_met_by_id[row["entity_id"]] = (row["starts_at"], row["title"])

    live_event_ids: set[str] = set()
    events_written = 0
    attendees_total = 0

    for row in event_rows:
        event_id = str(row["event_id"])
        live_event_ids.add(event_id)

        attendees: list[PrepAttendee] = []
        for eid in row["entity_ids"] or []:
            if eid is None:
                continue
            name = names_by_id.get(eid)
            if name is None:
                # Linked entity vanished from the registry — skip rather than emit
                # a nameless attendee.
                continue
            last_met = last_met_by_id.get(eid)
            attendees.append(
                {
                    "entity_id": str(eid),
                    "name": name,
                    "dunbar_tier": tier_by_id.get(eid),
                    "notes": notes_by_id.get(eid, []),
                    "last_met": last_met[0].isoformat() if last_met else None,
                    "last_met_event": last_met[1] if last_met else None,
                    "message_context": [],
                }
            )

        attendees.sort(key=lambda a: a["name"].lower())
        attendees_total += len(attendees)

        envelope: PrepContribution = {
            "butler": "relationship",
            "event_id": event_id,
            "event_title": str(row["title"] or "Untitled"),
            "event_starts_at": row["starts_at"].isoformat(),
            "has_context": len(attendees) > 0,
            "attendees": attendees,
        }
        await state_set(pool, prep_key(event_id), envelope)
        events_written += 1

    pruned = await prune_old_prep_contributions(pool, live_event_ids=live_event_ids)

    logger.info(
        "Relationship calendar prep contribution: date=%s events_written=%d attendees=%d pruned=%d",
        today_str,
        events_written,
        attendees_total,
        pruned,
    )

    return {
        "butler": "relationship",
        "date": today_str,
        "events_written": events_written,
        "attendees": attendees_total,
        "pruned": pruned,
    }


# ---------------------------------------------------------------------------
# Email/message-context prep contribution job (messenger / travel)
# ---------------------------------------------------------------------------


def _truncate_snippet(text: str) -> str:
    """Collapse whitespace and truncate a message body to a one-line snippet."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= SNIPPET_MAX_LEN:
        return collapsed
    return collapsed[: SNIPPET_MAX_LEN - 1].rstrip() + "…"


async def _fetch_message_context_by_entity(
    pool: asyncpg.Pool,
    entity_ids: list[Any],
    *,
    since: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Return the most-recent email threads per attendee entity from message_inbox.

    Reads ``switchboard.message_inbox`` — the persistent inbound-message store the
    email/message-owning butlers can see — grouping inbound ``email``-channel
    messages by the resolved sender entity (``source_sender_entity_id``) and the
    thread identity. The sender of an inbound email is the attendee, so grouping
    by the resolved sender entity yields the threads that attendee wrote, keyed by
    the same ``entity_id`` the prep merge joins on.

    This is the deterministic precompute read (a scheduled job reading the message
    store), NOT a request-time cross-schema read. It is fail-open: if the
    ``switchboard.message_inbox`` table is unreadable (absent / no grant) the job
    surfaces no message context this run rather than raising.

    Returns ``{entity_id_str: [thread_context, ...]}`` capped at
    :data:`MAX_THREADS_PER_ATTENDEE` most-recent threads per entity.
    """
    if not entity_ids:
        return {}

    entity_id_strs = [str(eid) for eid in entity_ids]
    try:
        rows = await pool.fetch(
            """
            SELECT
                COALESCE(request_context ->> 'source_sender_entity_id',
                         request_context ->> 'source_entity_id')         AS entity_id,
                COALESCE(request_context ->> 'source_thread_identity',
                         id::text)                                       AS thread_identity,
                MAX(received_at)                                         AS last_message_at,
                COUNT(*)                                                 AS message_count,
                (array_agg(request_context ->> 'subject'
                           ORDER BY received_at DESC))[1]                AS subject,
                (array_agg(normalized_text
                           ORDER BY received_at DESC))[1]               AS latest_text
            FROM switchboard.message_inbox
            WHERE direction = 'inbound'
              AND request_context ->> 'source_channel' = 'email'
              AND COALESCE(request_context ->> 'source_sender_entity_id',
                           request_context ->> 'source_entity_id') = ANY($1::text[])
              AND received_at >= $2
            GROUP BY entity_id, thread_identity
            ORDER BY entity_id, last_message_at DESC
            """,
            entity_id_strs,
            since,
        )
    except Exception:
        logger.warning(
            "calendar prep message-context: switchboard.message_inbox read failed; "
            "surfacing no message context this run",
            exc_info=True,
        )
        return {}

    threads_by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        eid = row["entity_id"]
        if not eid:
            continue
        bucket = threads_by_entity.setdefault(eid, [])
        if len(bucket) >= MAX_THREADS_PER_ATTENDEE:
            continue
        snippet = _truncate_snippet(row["latest_text"] or "")
        subject = (row["subject"] or "").strip()
        if not subject:
            subject = snippet[:80] if snippet else "(no subject)"
        last_message_at = row["last_message_at"]
        bucket.append(
            {
                "channel": "email",
                "thread_id": row["thread_identity"],
                "subject": subject,
                "snippet": snippet,
                "last_message_at": (
                    last_message_at.isoformat() if last_message_at is not None else None
                ),
                "message_count": int(row["message_count"]),
            }
        )
    return threads_by_entity


async def run_email_calendar_prep_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
    *,
    butler_name: str,
) -> dict[str, Any]:
    """Email/message-owning butler calendar meeting-prep contribution (deterministic, zero LLM).

    Companion to :func:`run_relationship_calendar_prep_contribution` for the
    butlers that own the email/message channel (messenger, travel). For each
    entity-linked event in the rolling lookahead window, it precomputes the
    ``message_context`` panel — the recent email threads each attendee wrote —
    and writes one envelope per event under ``calendar/prep/<event_id>`` into the
    butler's OWN ``state`` store. The existing prep-rail read endpoint merges this
    envelope into the relationship-sourced attendees by ``entity_id`` through the
    cross-schema ``calendar.v_prep_contributions`` view.

    Per RFC-0010/0020 there is NO direct cross-butler Gmail read at request time
    and NO per-request LLM: the recent-threads precompute reads the persisted
    inbound-message store (``switchboard.message_inbox``) during this scheduled
    job, and the read endpoint serves only the cached view.

    To preserve the prep rail's honest empty-state, an envelope is written ONLY
    for events where at least one attendee has recent message context; events with
    no message context are skipped and any previously-written key is pruned.
    """
    del job_args

    today = today_sgt()
    today_str = today.isoformat()
    now_utc = datetime.now(tz=UTC)

    sgt_midnight = datetime(today.year, today.month, today.day, tzinfo=SGT)
    window_start = sgt_midnight.astimezone(UTC)
    window_end = (sgt_midnight + timedelta(days=PREP_LOOKAHEAD_DAYS + 1)).astimezone(UTC)
    message_since = now_utc - timedelta(days=MESSAGE_CONTEXT_LOOKBACK_DAYS)

    # --- Upcoming entity-linked events in the window (own calendar projection) ---
    event_rows = await pool.fetch(
        """
        SELECT ce.id AS event_id,
               ce.title AS title,
               ce.starts_at AS starts_at,
               array_agg(DISTINCT cee.entity_id) AS entity_ids
        FROM calendar_event_entities cee
        JOIN calendar_events ce ON ce.id = cee.event_id
        WHERE ce.starts_at >= $1
          AND ce.starts_at < $2
          AND COALESCE(ce.status, 'confirmed') <> 'cancelled'
        GROUP BY ce.id, ce.title, ce.starts_at
        ORDER BY ce.starts_at ASC
        """,
        window_start,
        window_end,
    )

    all_entity_ids: set[Any] = set()
    for row in event_rows:
        for eid in row["entity_ids"] or []:
            if eid is not None:
                all_entity_ids.add(eid)

    names_by_id: dict[Any, str] = {}
    if all_entity_ids:
        entity_id_list = list(all_entity_ids)
        name_rows = await pool.fetch(
            """
            SELECT id, COALESCE(canonical_name, 'Unknown') AS name
            FROM public.entities
            WHERE id = ANY($1::uuid[])
            """,
            entity_id_list,
        )
        for row in name_rows:
            names_by_id[row["id"]] = row["name"]

    threads_by_entity = await _fetch_message_context_by_entity(
        pool, list(all_entity_ids), since=message_since
    )

    live_event_ids: set[str] = set()
    events_written = 0
    attendees_total = 0
    threads_total = 0

    for row in event_rows:
        event_id = str(row["event_id"])

        attendees: list[PrepAttendee] = []
        for eid in row["entity_ids"] or []:
            if eid is None:
                continue
            name = names_by_id.get(eid)
            if name is None:
                continue
            threads = threads_by_entity.get(str(eid))
            if not threads:
                # Email envelope only carries attendees with message context, so an
                # event with no recent threads contributes nothing (honest empty-state).
                continue
            attendees.append(
                {
                    "entity_id": str(eid),
                    "name": name,
                    "dunbar_tier": None,
                    "notes": [],
                    "last_met": None,
                    "last_met_event": None,
                    "message_context": threads,
                }
            )
            threads_total += len(threads)

        if not attendees:
            continue

        attendees.sort(key=lambda a: a["name"].lower())
        attendees_total += len(attendees)
        live_event_ids.add(event_id)

        envelope: PrepContribution = {
            "butler": butler_name,
            "event_id": event_id,
            "event_title": str(row["title"] or "Untitled"),
            "event_starts_at": row["starts_at"].isoformat(),
            "has_context": True,
            "attendees": attendees,
        }
        await state_set(pool, prep_key(event_id), envelope)
        events_written += 1

    pruned = await prune_old_prep_contributions(pool, live_event_ids=live_event_ids)

    logger.info(
        "%s calendar prep message-context: date=%s events_written=%d attendees=%d "
        "threads=%d pruned=%d",
        butler_name,
        today_str,
        events_written,
        attendees_total,
        threads_total,
        pruned,
    )

    return {
        "butler": butler_name,
        "date": today_str,
        "events_written": events_written,
        "attendees": attendees_total,
        "threads": threads_total,
        "pruned": pruned,
    }


async def run_messenger_calendar_prep_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Messenger butler calendar meeting-prep message-context contribution job."""
    return await run_email_calendar_prep_contribution(pool, job_args, butler_name="messenger")


async def run_travel_calendar_prep_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Travel butler calendar meeting-prep message-context contribution job."""
    return await run_email_calendar_prep_contribution(pool, job_args, butler_name="travel")
