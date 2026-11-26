"""Anchored reminders follow their event (bu-5ii4gj.10 slice R-S2, REQ-module-calendar-057).

When a provider event is written to the projection, every one-time reminder anchored to it
(``metadata.anchor``, slice R-S1) is checked against the event as it now stands:

* the event was cancelled, or the owner no longer attends it (declined, or a quarantined
  invitation): the reminder is **retired** (cancelled, reason ``anchor_cancelled``);
* the occurrence moved: the reminder is **rebased** to the new start plus its offset and the stored
  anchor follows the event.

Each change writes one ``reminder_receipts`` row in the same transaction, unique on (reminder,
kind, revision), so a replayed sync changes nothing and announces nothing a second time. For each
new receipt exactly one insight candidate is proposed through the Switchboard broker, never a
direct notify: a timed line when the reminder is now under two hours away (falling back to the
digest while the timed lane is dark), a digest line otherwise. The candidate carries no premise:
``occurrence_still_changed`` answers a different question than whether this reminder's time is
still right. Recurring anchors are not followed yet (an occurrence is identified by its original
start, which a moved instance no longer has). Nothing here raises: the event write has committed.
"""

from __future__ import annotations

import hashlib
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
#: A reminder this close to firing is announced on the timed lane, otherwise in the digest.
TIMED_WITHIN = timedelta(hours=2)
CANDIDATE_PRIORITY = 55
CANDIDATE_CATEGORY = "calendar_reminder"

_EVENT_SQL = (
    "SELECT origin_ref, status, starts_at, recurrence_rule, metadata FROM calendar_events "
    "WHERE id = $1 "
    "-- quarantine-exempt: reads metadata only, filtered by is_owner_attending"
)
_REMINDERS_SQL = """
SELECT r.id, r.title, r.starts_at, r.ends_at, r.metadata -> 'anchor' AS anchor
FROM calendar_events r
JOIN calendar_sources s ON s.id = r.source_id
WHERE s.source_kind = $2
  AND r.recurrence_rule IS NULL
  AND r.status = 'confirmed'
  AND r.metadata ->> 'native' = 'true'
  AND r.metadata -> 'anchor' ->> 'event_origin_ref' = $1
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


def _revision(kind: str, starts_at: datetime) -> str:
    return "retired" if kind == "retired" else starts_at.astimezone(UTC).isoformat()


async def _apply(
    conn: Any,
    reminder: Any,
    anchor: dict[str, Any],
    *,
    event_start: datetime,
    retire: bool,
) -> dict[str, Any] | None:
    """Retire or rebase one reminder and write its receipt; None when already applied."""
    old_start: datetime = reminder["starts_at"]
    kind = "retired" if retire else "rebased"
    new_start = None if retire else event_start + timedelta(minutes=anchor["offset_minutes"])
    receipt = await conn.fetchval(
        """
        INSERT INTO reminder_receipts
            (reminder_event_id, kind, revision, old_starts_at, new_starts_at, reason)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (reminder_event_id, kind, revision) DO NOTHING
        RETURNING id
        """,
        reminder["id"],
        kind,
        _revision(kind, event_start),
        old_start,
        new_start,
        "anchor_cancelled" if retire else "anchor_moved",
    )
    if receipt is None:
        return None
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
    return {
        "reminder_id": reminder["id"],
        "title": reminder["title"],
        "kind": kind,
        "revision": _revision(kind, event_start),
        "new_start": new_start,
    }


def _line(change: dict[str, Any]) -> str:
    from butlers.jobs.meeting_card import inert_text

    title = inert_text(change["title"], 80) or "your reminder"
    if change["kind"] == "retired":
        return f"Reminder cancelled: {title}. Its event was cancelled or you declined it."
    when = change["new_start"].astimezone(UTC)
    return f"Reminder moved: {title} is now {when:%a %d %b %H:%M} UTC, following its event."


async def _announce(pool: Any, change: dict[str, Any], origin_butler: str, now: datetime) -> str:
    """Propose the one candidate for a change. Returns the broker's status (or ``skipped``)."""
    try:
        broker = importlib.import_module("butlers.tools.switchboard.insight.broker")
    except Exception:
        logger.warning("reminder follow: insight broker unavailable", exc_info=True)
        return "skipped"
    digest = hashlib.sha256(f"{change['kind']}|{change['revision']}".encode()).hexdigest()[:12]
    key = f"{origin_butler}:reminder-{change['kind']}:{change['reminder_id']}:{digest}"
    fire_at = change["new_start"] or now
    common: dict[str, Any] = {
        "origin_butler": origin_butler,
        "priority": CANDIDATE_PRIORITY,
        "category": CANDIDATE_CATEGORY,
        "dedup_key": key,
        "message": _line(change),
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


async def follow_anchored_reminders(
    pool: asyncpg.Pool, event_id: uuid.UUID, *, origin_butler: str | None = None
) -> list[str]:
    """Rebase or retire the reminders anchored to one provider event. Never raises.

    Returns the receipt kinds written this call (``rebased`` / ``retired``), in reminder order.
    """
    try:
        event = await pool.fetchrow(_EVENT_SQL, event_id)
        if event is None or not event["origin_ref"] or event["recurrence_rule"]:
            return []
        metadata = _metadata_object(event["metadata"])
        if metadata.get("source_type") != "provider_event":
            return []
        retire = event["status"] == "cancelled" or not is_owner_attending(metadata)
        reminders = await pool.fetch(_REMINDERS_SQL, event["origin_ref"], SOURCE_KIND_REMINDERS)
        changes: list[dict[str, Any]] = []
        async with pool.acquire() as conn, conn.transaction():
            for reminder in reminders:
                anchor = _anchor(reminder["anchor"])
                if anchor is None:
                    continue
                original = datetime.fromisoformat(anchor["original_start"])
                if not retire and event["starts_at"] == original:
                    continue
                change = await _apply(
                    conn, reminder, anchor, event_start=event["starts_at"], retire=retire
                )
                if change is not None:
                    changes.append(change)
        now = datetime.now(UTC)
        for change in changes:
            try:
                await _announce(pool, change, origin_butler or "general", now)
            except Exception:
                logger.warning("reminder follow: announcement failed", exc_info=True)
        return [c["kind"] for c in changes]
    except Exception as exc:  # the event write has already committed; this must not undo it
        logger.warning("reminder_follow_failed error=%s", type(exc).__name__)
        return []
