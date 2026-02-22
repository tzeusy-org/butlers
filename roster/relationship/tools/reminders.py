"""Reminders — create, list, and dismiss reminders for contacts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from dateutil.relativedelta import relativedelta

from butlers.tools.relationship._schema import table_columns
from butlers.tools.relationship.feed import _log_activity


def _legacy_from_new_type(reminder_type: str) -> str:
    if reminder_type in {"recurring_yearly", "recurring_monthly"}:
        return "recurring"
    return reminder_type


def _new_from_legacy_type(reminder_type: str) -> str:
    if reminder_type == "recurring":
        return "recurring_monthly"
    return reminder_type


def _normalize_reminder_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    if "message" not in result and "label" in result:
        result["message"] = result["label"]
    if "label" not in result and "message" in result:
        result["label"] = result["message"]
    if "reminder_type" not in result and "type" in result:
        result["reminder_type"] = _legacy_from_new_type(result["type"])
    if "type" not in result and "reminder_type" in result:
        result["type"] = _new_from_legacy_type(result["reminder_type"])
    if "due_at" not in result and "next_trigger_at" in result:
        result["due_at"] = result["next_trigger_at"]
    if "next_trigger_at" not in result and "due_at" in result:
        result["next_trigger_at"] = result["due_at"]
    if "dismissed" not in result:
        result["dismissed"] = result.get("next_trigger_at") is None
    return result


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
    cols = await table_columns(pool, "reminders")

    effective_label = label or message or ""
    effective_type = type or _new_from_legacy_type(reminder_type or "one_time")
    effective_next_trigger_at = next_trigger_at if next_trigger_at is not None else due_at
    effective_message = message or label or ""
    effective_reminder_type = reminder_type or _legacy_from_new_type(effective_type)
    effective_timezone = (timezone or "UTC").strip() or "UTC"

    insert_cols: list[str] = []
    values: list[Any] = []

    def add(col: str, val: Any) -> None:
        insert_cols.append(col)
        values.append(val)

    if "contact_id" in cols:
        add("contact_id", contact_id)
    if "message" in cols:
        add("message", effective_message)
    if "reminder_type" in cols:
        add("reminder_type", effective_reminder_type)
    if "cron" in cols:
        add("cron", cron)
    if "due_at" in cols:
        add("due_at", due_at if due_at is not None else effective_next_trigger_at)
    if "label" in cols:
        add("label", effective_label)
    if "type" in cols:
        add("type", effective_type)
    if "next_trigger_at" in cols:
        add("next_trigger_at", effective_next_trigger_at)
    if "timezone" in cols:
        add("timezone", effective_timezone)
    if "until_at" in cols:
        add("until_at", until_at)
    if "calendar_event_id" in cols:
        add("calendar_event_id", calendar_event_id)

    placeholders = [f"${idx}" for idx in range(1, len(values) + 1)]
    row = await pool.fetchrow(
        f"""
        INSERT INTO reminders ({", ".join(insert_cols)})
        VALUES ({", ".join(placeholders)})
        RETURNING *
        """,
        *values,
    )
    result = _normalize_reminder_row(dict(row))
    if contact_id is not None:
        await _log_activity(
            pool,
            contact_id,
            "reminder_created",
            f"Created reminder: '{effective_label}'",
            entity_type="reminder",
            entity_id=result["id"],
        )
    return result


async def reminder_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    include_dismissed: bool = False,
) -> list[dict[str, Any]]:
    """List reminders, optionally filtered by contact."""
    cols = await table_columns(pool, "reminders")
    where: list[str] = []
    args: list[Any] = []

    if contact_id is not None:
        args.append(contact_id)
        where.append(f"contact_id = ${len(args)}")

    if not include_dismissed:
        if "dismissed" in cols:
            where.append("dismissed = false")
        elif "next_trigger_at" in cols:
            where.append("next_trigger_at IS NOT NULL")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    if "next_trigger_at" in cols:
        order_sql = "ORDER BY next_trigger_at ASC NULLS LAST"
    else:
        order_sql = "ORDER BY created_at DESC"

    rows = await pool.fetch(
        f"SELECT * FROM reminders {where_sql} {order_sql}",
        *args,
    )
    return [_normalize_reminder_row(dict(row)) for row in rows]


async def reminder_dismiss(pool: asyncpg.Pool, reminder_id: uuid.UUID) -> dict[str, Any]:
    """Dismiss a reminder across legacy/spec schemas."""
    row = await pool.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
    if row is None:
        raise ValueError(f"Reminder {reminder_id} not found")
    cols = await table_columns(pool, "reminders")
    original = _normalize_reminder_row(dict(row))

    if "dismissed" in cols:
        if "updated_at" in cols:
            updated = await pool.fetchrow(
                """
                UPDATE reminders
                SET dismissed = true, updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                reminder_id,
            )
        else:
            updated = await pool.fetchrow(
                """
                UPDATE reminders SET dismissed = true
                WHERE id = $1
                RETURNING *
                """,
                reminder_id,
            )
        result = _normalize_reminder_row(dict(updated))
    else:
        now = datetime.now(UTC)
        reminder_type = original["type"]
        next_at = original.get("next_trigger_at")

        if reminder_type == "one_time" or next_at is None:
            new_next = None
        elif reminder_type == "recurring_yearly":
            new_next = next_at + relativedelta(years=1)
        elif reminder_type == "recurring_monthly":
            new_next = next_at + relativedelta(months=1)
        else:
            new_next = None

        if "updated_at" in cols:
            updated = await pool.fetchrow(
                """
                UPDATE reminders
                SET last_triggered_at = $2, next_trigger_at = $3, updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                reminder_id,
                now,
                new_next,
            )
        else:
            updated = await pool.fetchrow(
                """
                UPDATE reminders
                SET last_triggered_at = $2, next_trigger_at = $3
                WHERE id = $1
                RETURNING *
                """,
                reminder_id,
                now,
                new_next,
            )
        result = _normalize_reminder_row(dict(updated))

    if result.get("contact_id") is not None:
        await _log_activity(
            pool,
            result["contact_id"],
            "reminder_dismissed",
            f"Dismissed reminder: '{original['label']}'",
            entity_type="reminder",
            entity_id=reminder_id,
        )
    return result
