"""Important dates — add, list, and find upcoming dates for contacts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import contact_name_expr, table_columns
from butlers.tools.relationship.feed import _log_activity


async def date_add(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    label: str,
    month: int,
    day: int,
    year: int | None = None,
) -> dict[str, Any]:
    """Add an important date for a contact. Skips duplicate contact+label+month+day."""
    # Idempotency guard: check for existing duplicate
    existing = await pool.fetchrow(
        """
        SELECT id FROM important_dates
        WHERE contact_id = $1 AND label = $2 AND month = $3 AND day = $4
        """,
        contact_id,
        label,
        month,
        day,
    )
    if existing is not None:
        return {"skipped": "duplicate", "existing_id": str(existing["id"])}

    row = await pool.fetchrow(
        """
        INSERT INTO important_dates (contact_id, label, month, day, year)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        contact_id,
        label,
        month,
        day,
        year,
    )
    result = dict(row)
    await _log_activity(
        pool, contact_id, "date_added", f"Added important date '{label}' ({month}/{day})"
    )
    return result


async def date_list(pool: asyncpg.Pool, contact_id: uuid.UUID) -> list[dict[str, Any]]:
    """List all important dates for a contact."""
    rows = await pool.fetch(
        "SELECT * FROM important_dates WHERE contact_id = $1 ORDER BY month, day",
        contact_id,
    )
    return [dict(row) for row in rows]


async def upcoming_dates(pool: asyncpg.Pool, days_ahead: int = 30) -> list[dict[str, Any]]:
    """Get upcoming important dates within the next N days using month/day matching."""
    from datetime import date

    now = datetime.now(UTC)
    today = now.date()
    end_date = today + timedelta(days=days_ahead)

    contact_cols = await table_columns(pool, "contacts")
    name_sql = contact_name_expr(contact_cols, alias="c")
    rows = await pool.fetch(
        f"""
        SELECT d.*, {name_sql} as contact_name
        FROM important_dates d
        JOIN contacts c ON d.contact_id = c.id
        WHERE c.archived_at IS NULL
        ORDER BY d.month, d.day
        """
    )

    results = []
    for row in rows:
        d = dict(row)
        # Check if this month/day falls within our window
        # Try current year first, then next year for wrapping
        for try_year in [today.year, today.year + 1]:
            try:
                candidate = date(try_year, d["month"], d["day"])
                if today <= candidate <= end_date:
                    d["upcoming_date"] = candidate.isoformat()
                    results.append(d)
                    break
            except ValueError:
                # Invalid date (e.g., Feb 30)
                continue

    return results
