"""Database operations for time-aware delivery (delivery preferences + deferred notifications).

Provides async functions for:
  - get_delivery_preferences     — Fetch delivery preferences for a butler
  - upsert_delivery_preferences  — Create or update delivery preferences
  - insert_deferred_notification — Persist a deferred notification
  - list_deferred_notifications  — List deferred notifications with status filter
  - cancel_deferred_notification — Cancel a pending deferred notification

These functions require an asyncpg pool and operate within the current butler schema.
See: openspec/changes/temporal-intelligence/specs/time-aware-delivery/spec.md
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

_VALID_PRIORITIES = frozenset({"high", "medium", "low"})
_VALID_STATUSES = frozenset({"pending", "delivered", "expired", "cancelled"})


def validate_timezone(tz_name: str) -> str:
    """Validate and normalise a timezone name.

    Raises:
        ValueError: If the timezone is not recognised.
    """
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {tz_name!r}") from exc
    return tz_name


async def get_delivery_preferences(
    pool: asyncpg.Pool,
    butler_name: str,
) -> dict[str, Any] | None:
    """Fetch delivery preferences for the current butler schema.

    Args:
        pool: asyncpg connection pool.
        butler_name: The butler's canonical name (used as the lookup key).

    Returns a dict of preference fields, or None if no row exists.
    """
    row = await pool.fetchrow(
        """
        SELECT id, butler_name, quiet_hours_start, quiet_hours_end, timezone,
               batch_low_priority, batch_delivery_time, override_channels,
               created_at, updated_at
        FROM delivery_preferences
        WHERE butler_name = $1
        """,
        butler_name,
    )
    if row is None:
        return None
    result = dict(row)
    # Normalise TIME columns to "HH:MM" strings
    for col in ("quiet_hours_start", "quiet_hours_end", "batch_delivery_time"):
        val = result.get(col)
        if val is not None and not isinstance(val, str):
            # asyncpg may return a datetime.time object
            result[col] = val.strftime("%H:%M")
    # Normalise UUID to string
    if result.get("id") is not None:
        result["id"] = str(result["id"])
    # Normalise JSONB
    override_channels = result.get("override_channels")
    if isinstance(override_channels, str):
        result["override_channels"] = json.loads(override_channels)
    # Normalise timestamps
    for col in ("created_at", "updated_at"):
        val = result.get(col)
        if val is not None and isinstance(val, datetime):
            result[col] = val.isoformat()
    return result


async def upsert_delivery_preferences(
    pool: asyncpg.Pool,
    butler_name: str,
    *,
    timezone: str | None = None,
    quiet_hours_start: str | None = None,
    quiet_hours_end: str | None = None,
    batch_low_priority: bool | None = None,
    batch_delivery_time: str | None = None,
    override_channels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update delivery preferences for this butler.

    Only provided (non-None) fields are written. On first call a row is created
    with defaults for unspecified fields.

    Args:
        pool: asyncpg connection pool.
        butler_name: The butler's canonical name (stored in the row).
        timezone: IANA timezone string (validated).
        quiet_hours_start: Quiet hours start time in 'HH:MM' format.
        quiet_hours_end: Quiet hours end time in 'HH:MM' format.
        batch_low_priority: If True, batch low-priority notifications.
        batch_delivery_time: Batch delivery time in 'HH:MM' format.
        override_channels: Per-channel quiet hours overrides (JSONB).

    Returns:
        The upserted row as a dict.

    Raises:
        ValueError: If timezone is invalid.
    """
    if timezone is not None:
        validate_timezone(timezone)

    # Build SET clause dynamically for provided fields only.
    updates: list[str] = ["updated_at = now()"]
    params: list[Any] = [butler_name]  # $1 = butler_name

    def _add(col: str, value: Any) -> None:
        params.append(value)
        updates.append(f"{col} = ${len(params)}")

    if timezone is not None:
        _add("timezone", timezone)
    if quiet_hours_start is not None:
        _add("quiet_hours_start", quiet_hours_start)
    if quiet_hours_end is not None:
        _add("quiet_hours_end", quiet_hours_end)
    if batch_low_priority is not None:
        _add("batch_low_priority", batch_low_priority)
    if batch_delivery_time is not None:
        _add("batch_delivery_time", batch_delivery_time)
    if override_channels is not None:
        _add("override_channels", json.dumps(override_channels))

    set_clause = ", ".join(updates)

    row = await pool.fetchrow(
        f"""
        INSERT INTO delivery_preferences (butler_name)
        VALUES ($1)
        ON CONFLICT (butler_name) DO UPDATE
            SET {set_clause}
        RETURNING id, butler_name, quiet_hours_start, quiet_hours_end, timezone,
                  batch_low_priority, batch_delivery_time, override_channels,
                  created_at, updated_at
        """,
        *params,
    )
    result = dict(row)
    for col in ("quiet_hours_start", "quiet_hours_end", "batch_delivery_time"):
        val = result.get(col)
        if val is not None and not isinstance(val, str):
            result[col] = val.strftime("%H:%M")
    if result.get("id") is not None:
        result["id"] = str(result["id"])
    override_channels_val = result.get("override_channels")
    if isinstance(override_channels_val, str):
        result["override_channels"] = json.loads(override_channels_val)
    for col in ("created_at", "updated_at"):
        val = result.get(col)
        if val is not None and isinstance(val, datetime):
            result[col] = val.isoformat()
    return result


async def insert_deferred_notification(
    pool: asyncpg.Pool,
    *,
    butler_name: str,
    channel: str,
    message: str,
    priority: str,
    envelope: dict[str, Any],
    deliver_at: datetime,
    deferred_at: datetime | None = None,
) -> str:
    """Persist a deferred notification to the database.

    Args:
        pool: asyncpg connection pool.
        butler_name: The butler deferring the notification.
        channel: Delivery channel (e.g. 'telegram', 'email').
        message: The notification message text.
        priority: Notification priority ('high', 'medium', 'low').
        envelope: Full notify.v1 envelope dict.
        deliver_at: UTC datetime when the notification should be delivered.
        deferred_at: UTC datetime when the notification was deferred (default: now()).

    Returns:
        The UUID of the inserted row as a string.
    """
    if priority not in _VALID_PRIORITIES:
        raise ValueError(
            f"Invalid priority {priority!r}; expected one of {sorted(_VALID_PRIORITIES)}"
        )

    notif_id = await pool.fetchval(
        """
        INSERT INTO deferred_notifications
            (butler_name, channel, message, priority, envelope, deliver_at, deferred_at, status)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, COALESCE($7, now()), 'pending')
        RETURNING id
        """,
        butler_name,
        channel,
        message,
        priority,
        json.dumps(envelope),
        deliver_at,
        deferred_at,
    )
    return str(notif_id)


async def list_deferred_notifications(
    pool: asyncpg.Pool,
    *,
    butler_name: str,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List deferred notifications for a butler.

    Args:
        pool: asyncpg connection pool.
        butler_name: Filter to this butler's notifications.
        status: Optional status filter ('pending', 'delivered', 'expired', 'cancelled').
        limit: Maximum number of rows to return (default 100).

    Returns:
        List of notification dicts ordered by deliver_at ASC.

    Raises:
        ValueError: If status filter is not a valid status value.
    """
    if status is not None and status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status filter {status!r}; expected one of {sorted(_VALID_STATUSES)}"
        )

    if status is not None:
        rows = await pool.fetch(
            """
            SELECT id, butler_name, channel, message, priority, envelope,
                   deferred_at, deliver_at, status, delivered_at
            FROM deferred_notifications
            WHERE butler_name = $1 AND status = $2
            ORDER BY deliver_at ASC
            LIMIT $3
            """,
            butler_name,
            status,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, butler_name, channel, message, priority, envelope,
                   deferred_at, deliver_at, status, delivered_at
            FROM deferred_notifications
            WHERE butler_name = $1
            ORDER BY deliver_at ASC
            LIMIT $2
            """,
            butler_name,
            limit,
        )

    results = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        envelope_val = item.get("envelope")
        if isinstance(envelope_val, str):
            item["envelope"] = json.loads(envelope_val)
        for col in ("deferred_at", "deliver_at", "delivered_at"):
            val = item.get(col)
            if val is not None and isinstance(val, datetime):
                item[col] = val.isoformat()
        results.append(item)
    return results


async def cancel_deferred_notification(
    pool: asyncpg.Pool,
    notification_id: str,
    *,
    butler_name: str,
) -> bool:
    """Cancel a pending deferred notification.

    Only pending notifications may be cancelled. Delivered/expired notifications
    cannot be cancelled.

    Args:
        pool: asyncpg connection pool.
        notification_id: UUID string of the notification to cancel.
        butler_name: Must match the notification's butler_name (ownership check).

    Returns:
        True if cancelled, False if not found or not cancellable.
    """
    try:
        notif_uuid = uuid.UUID(notification_id)
    except ValueError as exc:
        raise ValueError(f"Invalid notification_id: {notification_id!r}") from exc

    result = await pool.execute(
        """
        UPDATE deferred_notifications
        SET status = 'cancelled'
        WHERE id = $1 AND butler_name = $2 AND status = 'pending'
        """,
        notif_uuid,
        butler_name,
    )
    # asyncpg returns "UPDATE N" string
    updated_count = int(result.split()[-1])
    return updated_count > 0
