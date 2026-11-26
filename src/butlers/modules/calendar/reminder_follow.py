"""Anchored reminders follow their event (bu-5ii4gj.10 slice R-S2, REQ-module-calendar-057).

When a provider event is written to the projection, every one-time reminder anchored to it
(``metadata.anchor``, slice R-S1) is checked against the event as it now stands:

* the event was cancelled, or the owner no longer attends it (declined, or a quarantined
  invitation): the reminder is **retired** (cancelled, reason ``anchor_cancelled``);
* the occurrence moved: the reminder is **rebased** to the new start plus its offset and the stored
  anchor follows the event.

Only the owner's own copy of the event counts: the written copy's source must be an owner-role,
user-lane calendar, so a partner calendar's copy (where the partner declined) never retires or
moves the owner's reminder.

Each change writes one ``reminder_receipts`` row in the same transaction as the change, under a
row lock on the reminder. A replayed sync changes nothing because the stored anchor already
equals the event's start, not because the receipt is unique: an event that flips back and forth
rebases every time. For each receipt exactly one insight candidate is proposed through the
Switchboard broker, never a direct notify, and the receipt is marked announced only once the broker
took it: an unannounced receipt is retried by the next follow, so a broker outage loses
nothing. The line is a timed one when the reminder is now under two hours away (falling back to
the digest while the timed lane is dark), a digest line otherwise. The candidate carries no
premise: ``occurrence_still_changed`` answers a different question than whether this reminder's
time is still right. A moved occurrence of a recurring event is its own row (Google's exception
row), and the anchor names that row; the recurring master itself is not followed. Nothing here
raises: the event write has committed.
"""

from __future__ import annotations

import importlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.temporal.calendar_provenance import is_owner_attending
from butlers.modules.calendar.participants import _metadata_object

logger = logging.getLogger(__name__)

SOURCE_KIND_REMINDERS = "internal_reminders"
#: A receipt the broker did not take is retried on later sync passes this many times.
MAX_ANNOUNCE_ATTEMPTS = 5

#: A reminder this close to firing is announced on the timed lane, otherwise in the digest.
TIMED_WITHIN = timedelta(hours=2)
CANDIDATE_PRIORITY = 55
CANDIDATE_CATEGORY = "calendar_reminder"

_EVENT_SQL = (
    "SELECT e.origin_ref, e.status, e.starts_at, e.recurrence_rule, e.metadata, "
    "s.time_role, s.lane FROM calendar_events e JOIN calendar_sources s ON s.id = e.source_id "
    "WHERE e.id = $1 "
    "-- quarantine-exempt: reads metadata only, filtered by is_owner_attending"
)
_REMINDERS_SQL = """
SELECT r.id
FROM calendar_events r
JOIN calendar_sources s ON s.id = r.source_id
WHERE s.source_kind = $2
  AND r.recurrence_rule IS NULL
  AND r.status = 'confirmed'
  AND r.metadata ->> 'native' = 'true'
  AND r.metadata -> 'anchor' ->> 'event_origin_ref' = $1
-- quarantine-exempt: native reminders are never invitations
"""
_UNANNOUNCED_SQL = """
SELECT rr.id, rr.kind, rr.new_starts_at, r.id AS reminder_id, r.title
FROM reminder_receipts rr
JOIN calendar_events r ON r.id = rr.reminder_event_id
WHERE rr.announced_at IS NULL AND rr.attempts < $1
  AND rr.created_at > now() - interval '2 days'
ORDER BY rr.created_at
LIMIT 50
-- quarantine-exempt: native reminders are never invitations
"""


def _anchor(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or not raw.get("original_start"):
        return None
    if not isinstance(raw.get("offset_minutes"), int):
        return None
    return raw


async def _apply(
    conn: Any, reminder_id: Any, *, event_start: datetime, retire: bool
) -> uuid.UUID | None:
    """Retire or rebase one reminder and write its receipt; None when nothing needed changing.

    The reminder is locked and its stored anchor re-read, so two syncs racing on one event change
    it once, and a replay (the anchor already at the event's start) changes nothing.
    """
    reminder = await conn.fetchrow(
        "SELECT id, starts_at, ends_at, status, metadata -> 'anchor' AS anchor "
        "FROM calendar_events WHERE id = $1 FOR UPDATE "
        "-- quarantine-exempt: a native reminder is never an invitation",
        reminder_id,
    )
    anchor = _anchor(reminder["anchor"]) if reminder is not None else None
    if reminder is None or anchor is None or reminder["status"] != "confirmed":
        return None
    stored = datetime.fromisoformat(anchor["original_start"])
    if not retire and event_start == stored:
        return None
    old_start: datetime = reminder["starts_at"]
    new_start = None if retire else event_start + timedelta(minutes=anchor["offset_minutes"])
    receipt = await conn.fetchval(
        """
        INSERT INTO reminder_receipts
            (reminder_event_id, kind, revision, old_starts_at, new_starts_at, reason)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        reminder["id"],
        "retired" if retire else "rebased",
        "retired" if retire else f"{stored.isoformat()}>{event_start.isoformat()}",
        old_start,
        new_start,
        "anchor_cancelled" if retire else "anchor_moved",
    )
    if retire:
        await conn.execute(
            "UPDATE calendar_events SET status = 'cancelled', updated_at = now(), "
            "metadata = COALESCE(metadata, '{}'::jsonb) || $2::text::jsonb WHERE id = $1",
            reminder["id"],
            json.dumps({"cancel_reason": "anchor_cancelled"}),
        )
    else:
        assert new_start is not None
        shift = new_start - old_start
        await conn.execute(
            "UPDATE calendar_events SET starts_at = $2, ends_at = $3, updated_at = now(), "
            "metadata = COALESCE(metadata, '{}'::jsonb) || $4::text::jsonb WHERE id = $1",
            reminder["id"],
            new_start,
            reminder["ends_at"] + shift,
            json.dumps({"anchor": {**anchor, "original_start": event_start.isoformat()}}),
        )
        await conn.execute(
            "UPDATE calendar_event_instances SET starts_at = $2, ends_at = ends_at + $3 "
            "WHERE event_id = $1",
            reminder["id"],
            new_start,
            shift,
        )
    return receipt


def _line(change: dict[str, Any], zone: Any = UTC) -> str:
    from butlers.jobs.meeting_card import inert_text

    title = inert_text(change["title"], 80) or "your reminder"
    if change["kind"] == "retired":
        return f"Reminder cancelled: {title}. Its event was cancelled or you declined it."
    when = change["new_start"].astimezone(zone)
    return f"Reminder moved: {title} is now {when:%a %d %b %H:%M %Z}, following its event."


async def _announce(pool: Any, change: dict[str, Any], origin_butler: str, now: datetime) -> str:
    """Propose the one candidate for a change. Returns the broker's status (or ``skipped``)."""
    try:
        broker = importlib.import_module("butlers.tools.switchboard.insight.broker")
    except Exception:
        logger.warning("reminder follow: insight broker unavailable", exc_info=True)
        return "skipped"
    from butlers.jobs.briefing import owner_briefing_zone

    zone = await owner_briefing_zone(pool)  # owner-facing text is in the owner's zone
    key = f"{origin_butler}:reminder-{change['kind']}:{change['receipt_id']}"
    fire_at = change["new_start"] or now
    common: dict[str, Any] = {
        "origin_butler": origin_butler,
        "priority": CANDIDATE_PRIORITY,
        "category": CANDIDATE_CATEGORY,
        "dedup_key": key,
        "message": _line(change, zone),
        "premise": None,
        "now": now,
    }
    if change["new_start"] is not None and now <= fire_at <= now + TIMED_WITHIN:
        result = await broker.propose_insight_candidate(
            pool,
            expires_at=fire_at + timedelta(minutes=30),
            lane="timed",
            deliver_after=now,
            **common,
        )
        if result.get("status") != "error":
            return str(result.get("status"))
    result = await broker.propose_insight_candidate(
        pool, expires_at=max(fire_at, now) + timedelta(days=1), **common
    )
    return str(result.get("status"))


async def announce_pending_reminder_receipts(pool: Any, origin_butler: str | None) -> None:
    """Announce every receipt the broker has not yet taken. Never raises.

    Run once per successful sync pass, never per projected event. A broker that is down costs one
    failed call per pass (the sweep stops at the first failure and counts an attempt), and a
    receipt is tried at most ``MAX_ANNOUNCE_ATTEMPTS`` times. A broker that answers ``error`` or
    ``skipped`` has refused the receipt, so it is not retried.
    """
    origin_butler = origin_butler or "general"
    try:
        rows = await pool.fetch(_UNANNOUNCED_SQL, MAX_ANNOUNCE_ATTEMPTS)
    except Exception:
        logger.warning("reminder follow: unannounced receipts unreadable", exc_info=True)
        return
    now = datetime.now(UTC)
    for row in rows:
        change = {
            "receipt_id": row["id"],
            "reminder_id": row["reminder_id"],
            "title": row["title"],
            "kind": row["kind"],
            "new_start": row["new_starts_at"],
        }
        try:
            status = await _announce(pool, change, origin_butler, now)
            if status in ("error", "skipped"):
                await pool.execute(
                    "UPDATE reminder_receipts SET attempts = $2 WHERE id = $1",
                    row["id"],
                    MAX_ANNOUNCE_ATTEMPTS,
                )
            else:
                await pool.execute(
                    "UPDATE reminder_receipts SET announced_at = now() WHERE id = $1", row["id"]
                )
        except Exception:
            logger.warning("reminder follow: announcement failed", exc_info=True)
            try:
                await pool.execute(
                    "UPDATE reminder_receipts SET attempts = attempts + 1 WHERE id = $1",
                    row["id"],
                )
            except Exception:
                logger.warning("reminder follow: attempt not counted", exc_info=True)
            return  # the broker is down: the rest wait for the next pass


async def follow_anchored_reminders(
    pool: asyncpg.Pool, event_id: uuid.UUID, *, origin_butler: str | None = None
) -> list[str]:
    """Rebase or retire the reminders anchored to one provider event. Never raises.

    Returns the receipt kinds written this call (``rebased`` / ``retired``), in reminder order.
    Announcing is a separate once-per-pass sweep (``announce_pending_reminder_receipts``).
    """
    kinds: list[str] = []
    try:
        event = await pool.fetchrow(_EVENT_SQL, event_id)
        metadata = _metadata_object(event["metadata"]) if event is not None else {}
        if (
            event is not None
            and event["origin_ref"]
            and not event["recurrence_rule"]
            and event["lane"] == "user"
            and event["time_role"] == "owner"
            and metadata.get("source_type") == "provider_event"
        ):
            retire = event["status"] == "cancelled" or not is_owner_attending(metadata)
            reminders = await pool.fetch(_REMINDERS_SQL, event["origin_ref"], SOURCE_KIND_REMINDERS)
            async with pool.acquire() as conn, conn.transaction():
                for reminder in reminders:
                    receipt = await _apply(
                        conn, reminder["id"], event_start=event["starts_at"], retire=retire
                    )
                    if receipt is not None:
                        kinds.append("retired" if retire else "rebased")
    except Exception as exc:  # the event write has already committed; this must not undo it
        logger.warning("reminder_follow_failed error=%s", type(exc).__name__)
        return []
    return kinds
