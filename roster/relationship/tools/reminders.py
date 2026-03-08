"""Reminders — create, list, and dismiss reminders for contacts backed by SPO facts.

Each reminder is a property fact in the facts table (supersession by subject key):
  subject   = contact:{contact_id}:reminder:{reminder_uuid}
  predicate = 'reminder'
  content   = message/label
  metadata  = {type, cron, due_at, dismissed, timezone, until_at,
               calendar_event_id, last_triggered_at, next_trigger_at}
  valid_at  = NULL (property fact — dismiss updates supersede)
  scope     = 'relationship'
  entity_id = contact's entity UUID (resolved via contacts.entity_id)

The response shape is backward compatible with the legacy reminders table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from dateutil.relativedelta import relativedelta

from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id
from butlers.tools.relationship.feed import _log_activity

logger = logging.getLogger(__name__)

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _fact_to_reminder(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the reminders API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)

    next_at_str = meta.get("next_trigger_at") or meta.get("due_at")
    next_at = None
    if next_at_str:
        try:
            next_at = datetime.fromisoformat(next_at_str)
        except (ValueError, TypeError):
            pass

    until_at_str = meta.get("until_at")
    until_at = None
    if until_at_str:
        try:
            until_at = datetime.fromisoformat(until_at_str)
        except (ValueError, TypeError):
            pass

    lta_str = meta.get("last_triggered_at")
    lta = None
    if lta_str:
        try:
            lta = datetime.fromisoformat(lta_str)
        except (ValueError, TypeError):
            pass

    cal_id_str = meta.get("calendar_event_id")
    cal_id = None
    if cal_id_str:
        try:
            cal_id = uuid.UUID(str(cal_id_str))
        except (ValueError, AttributeError):
            pass

    # Extract contact_id from subject (format: contact:{contact_id}:reminder:{uuid})
    subject = row.get("subject", "")
    parts = subject.split(":")
    contact_id = None
    if len(parts) >= 2 and parts[0] == "contact":
        try:
            contact_id = uuid.UUID(parts[1])
        except (ValueError, AttributeError):
            pass

    reminder_type = meta.get("type", "one_time")
    dismissed = meta.get("dismissed", False)

    return {
        "id": row["id"],
        "contact_id": contact_id,
        "message": row.get("content", ""),
        "label": row.get("content", ""),
        "type": reminder_type,
        "cron": meta.get("cron"),
        "due_at": next_at,
        "next_trigger_at": next_at,
        "timezone": meta.get("timezone", "UTC"),
        "until_at": until_at,
        "calendar_event_id": cal_id,
        "dismissed": dismissed,
        "last_triggered_at": lta,
        "created_at": row.get("created_at"),
    }


async def reminder_create(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    message: str | None = None,
    reminder_type: str | None = None,
    cron: str | None = None,
    due_at: datetime | None = None,
    *,
    label: str | None = None,
    type: str | None = None,
    next_trigger_at: datetime | None = None,
    timezone: str | None = None,
    until_at: datetime | None = None,
    calendar_event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create a reminder for a contact."""
    from butlers.modules.memory.storage import store_fact

    effective_label = label or message or ""
    effective_type = type or reminder_type or "one_time"
    effective_next_trigger_at = next_trigger_at if next_trigger_at is not None else due_at
    effective_timezone = (timezone or "UTC").strip() or "UTC"
    now = datetime.now(UTC)

    entity_id = await resolve_contact_entity_id(pool, contact_id) if contact_id else None
    embedding_engine = _get_embedding_engine()

    # Unique reminder subject per creation — reminders don't supersede each other
    reminder_uuid = uuid.uuid4()
    subject = (
        f"contact:{contact_id}:reminder:{reminder_uuid}"
        if contact_id
        else f"reminder:{reminder_uuid}"
    )

    fact_metadata: dict[str, Any] = {
        "type": effective_type,
        "dismissed": False,
        "timezone": effective_timezone,
    }
    if cron is not None:
        fact_metadata["cron"] = cron
    if effective_next_trigger_at is not None:
        fact_metadata["next_trigger_at"] = effective_next_trigger_at.isoformat()
        fact_metadata["due_at"] = effective_next_trigger_at.isoformat()
    if until_at is not None:
        fact_metadata["until_at"] = until_at.isoformat()
    if calendar_event_id is not None:
        fact_metadata["calendar_event_id"] = str(calendar_event_id)

    fact_id = await store_fact(
        pool,
        subject=subject,
        predicate="reminder",
        content=effective_label,
        embedding_engine=embedding_engine,
        permanence="stable",
        scope="relationship",
        entity_id=entity_id,
        valid_at=None,  # property fact — dismiss updates will supersede
        metadata=fact_metadata,
    )

    result: dict[str, Any] = {
        "id": fact_id,
        "contact_id": contact_id,
        "message": effective_label,
        "label": effective_label,
        "type": effective_type,
        "cron": cron,
        "due_at": effective_next_trigger_at,
        "next_trigger_at": effective_next_trigger_at,
        "timezone": effective_timezone,
        "until_at": until_at,
        "calendar_event_id": calendar_event_id,
        "dismissed": False,
        "last_triggered_at": None,
        "created_at": now,
    }

    if contact_id is not None:
        await _log_activity(
            pool,
            contact_id,
            "reminder_created",
            f"Created reminder: '{effective_label}'",
            entity_type="reminder",
            entity_id=fact_id,
        )

    return result


async def reminder_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    include_dismissed: bool = False,
) -> list[dict[str, Any]]:
    """List reminders, optionally filtered by contact."""
    conditions = [
        "predicate = 'reminder'",
        "scope = 'relationship'",
        "validity = 'active'",
        "valid_at IS NULL",
    ]
    params: list[Any] = []
    idx = 1

    if contact_id is not None:
        conditions.append(f"subject LIKE ${idx}")
        params.append(f"contact:{contact_id}:reminder:%")
        idx += 1

    if not include_dismissed:
        conditions.append(
            "((metadata->>'dismissed')::boolean = false OR metadata->>'dismissed' IS NULL)"
        )

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT id, subject, content, created_at, metadata
        FROM facts
        WHERE {where}
        ORDER BY created_at DESC
        """,
        *params,
    )

    results = [_fact_to_reminder(dict(r)) for r in rows]

    if not include_dismissed:
        # Secondary filter: one_time reminders with no next_trigger_at are effectively dismissed
        results = [r for r in results if not r["dismissed"]]

    return results


async def reminder_dismiss(pool: asyncpg.Pool, reminder_id: uuid.UUID) -> dict[str, Any]:
    """Dismiss a reminder; advance recurring reminders to the next trigger."""
    from butlers.modules.memory.storage import store_fact

    row = await pool.fetchrow(
        "SELECT id, subject, content, metadata, entity_id FROM facts WHERE id = $1",
        reminder_id,
    )
    if row is None:
        raise ValueError(f"Reminder {reminder_id} not found")

    meta = row["metadata"] or {}
    if isinstance(meta, str):
        meta = json.loads(meta)

    now = datetime.now(UTC)
    reminder_type = meta.get("type", "one_time")
    next_at_str = meta.get("next_trigger_at")
    next_at = None
    if next_at_str:
        try:
            next_at = datetime.fromisoformat(next_at_str)
        except (ValueError, TypeError):
            pass

    if reminder_type == "one_time" or next_at is None:
        new_next = None
    elif reminder_type == "recurring_yearly":
        new_next = next_at + relativedelta(years=1)
    elif reminder_type == "recurring_monthly":
        new_next = next_at + relativedelta(months=1)
    else:
        new_next = None

    entity_id = row["entity_id"]
    embedding_engine = _get_embedding_engine()

    new_metadata = dict(meta)
    new_metadata["last_triggered_at"] = now.isoformat()
    new_metadata["next_trigger_at"] = new_next.isoformat() if new_next else None
    new_metadata["due_at"] = new_next.isoformat() if new_next else None
    new_metadata["dismissed"] = new_next is None

    new_fact_id = await store_fact(
        pool,
        subject=row["subject"],
        predicate="reminder",
        content=row["content"],
        embedding_engine=embedding_engine,
        permanence="stable",
        scope="relationship",
        entity_id=entity_id,
        valid_at=None,  # property fact — supersedes previous
        metadata=new_metadata,
    )

    # Extract contact_id from subject
    subject = row["subject"]
    parts = subject.split(":")
    contact_id = None
    if len(parts) >= 2 and parts[0] == "contact":
        try:
            contact_id = uuid.UUID(parts[1])
        except (ValueError, AttributeError):
            pass

    until_at_str = new_metadata.get("until_at")
    until_at = None
    if until_at_str:
        try:
            until_at = datetime.fromisoformat(until_at_str)
        except (ValueError, TypeError):
            pass

    cal_id_str = new_metadata.get("calendar_event_id")
    cal_id = None
    if cal_id_str:
        try:
            cal_id = uuid.UUID(str(cal_id_str))
        except (ValueError, AttributeError):
            pass

    result: dict[str, Any] = {
        "id": new_fact_id,
        "contact_id": contact_id,
        "message": row["content"],
        "label": row["content"],
        "type": reminder_type,
        "cron": new_metadata.get("cron"),
        "due_at": new_next,
        "next_trigger_at": new_next,
        "timezone": new_metadata.get("timezone", "UTC"),
        "until_at": until_at,
        "calendar_event_id": cal_id,
        "dismissed": new_next is None,
        "last_triggered_at": now,
        "created_at": now,
    }

    if contact_id is not None:
        await _log_activity(
            pool,
            contact_id,
            "reminder_dismissed",
            f"Dismissed reminder: '{row['content']}'",
            entity_type="reminder",
            entity_id=reminder_id,
        )

    return result
