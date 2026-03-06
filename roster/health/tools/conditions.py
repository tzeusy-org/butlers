"""Conditions and symptoms — track health conditions and log symptoms."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _normalize_end_date, _row_to_dict

logger = logging.getLogger(__name__)

VALID_CONDITION_STATUSES = {"active", "managed", "resolved"}


async def condition_add(
    pool: asyncpg.Pool,
    name: str,
    status: str = "active",
    diagnosed_at: datetime | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Add a health condition. Status must be one of: active, managed, resolved."""
    if status not in VALID_CONDITION_STATUSES:
        raise ValueError(
            f"Invalid condition status: {status!r}. "
            f"Must be one of: {', '.join(sorted(VALID_CONDITION_STATUSES))}"
        )
    row = await pool.fetchrow(
        """
        INSERT INTO conditions (name, status, diagnosed_at, notes)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        name,
        status,
        diagnosed_at,
        notes,
    )
    return _row_to_dict(row)


async def condition_list(
    pool: asyncpg.Pool,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List conditions, optionally filtered by status. Ordered by created_at descending."""
    if status is not None:
        rows = await pool.fetch(
            "SELECT * FROM conditions WHERE status = $1 ORDER BY created_at DESC",
            status,
        )
    else:
        rows = await pool.fetch("SELECT * FROM conditions ORDER BY created_at DESC")
    return [_row_to_dict(r) for r in rows]


async def condition_update(
    pool: asyncpg.Pool,
    condition_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """Update a condition. Allowed fields: name, status, diagnosed_at, notes.

    If status is provided, it must be one of: active, managed, resolved.
    """
    cond_uuid = uuid.UUID(condition_id) if isinstance(condition_id, str) else condition_id
    allowed = {"name", "status", "diagnosed_at", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}

    if not updates:
        raise ValueError("No valid fields to update")

    # Validate status if provided
    if "status" in updates and updates["status"] not in VALID_CONDITION_STATUSES:
        raise ValueError(
            f"Invalid condition status: {updates['status']!r}. "
            f"Must be one of: {', '.join(sorted(VALID_CONDITION_STATUSES))}"
        )

    set_parts: list[str] = []
    params: list[Any] = [cond_uuid]
    idx = 2

    for col, val in updates.items():
        set_parts.append(f"{col} = ${idx}")
        params.append(val)
        idx += 1

    set_parts.append("updated_at = now()")
    set_clause = ", ".join(set_parts)

    row = await pool.fetchrow(
        f"UPDATE conditions SET {set_clause} WHERE id = $1 RETURNING *",
        *params,
    )
    if row is None:
        raise ValueError(f"Condition {condition_id} not found")
    return _row_to_dict(row)


async def symptom_log(
    pool: asyncpg.Pool,
    name: str,
    severity: int,
    condition_id: str | None = None,
    notes: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Log a symptom with severity (1-10), optionally linked to a condition."""
    if not (1 <= severity <= 10):
        raise ValueError(f"Severity must be between 1 and 10, got {severity}")

    cond_uuid = None
    if condition_id is not None:
        cond_uuid = uuid.UUID(condition_id) if isinstance(condition_id, str) else condition_id
        # Validate condition exists
        cond = await pool.fetchrow("SELECT id FROM conditions WHERE id = $1", cond_uuid)
        if cond is None:
            raise ValueError(f"Condition {condition_id} not found")

    row = await pool.fetchrow(
        """
        INSERT INTO symptoms (name, severity, condition_id, notes, occurred_at)
        VALUES ($1, $2, $3, $4, COALESCE($5, now()))
        RETURNING *
        """,
        name,
        severity,
        cond_uuid,
        notes,
        occurred_at,
    )
    return _row_to_dict(row)


async def symptom_history(
    pool: asyncpg.Pool,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get symptom history, optionally filtered by date range."""
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if start_date is not None:
        conditions.append(f"occurred_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"occurred_at <= ${idx}")
        params.append(_normalize_end_date(end_date))
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(
        f"SELECT * FROM symptoms {where} ORDER BY occurred_at DESC",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def symptom_search(
    pool: asyncpg.Pool,
    name: str | None = None,
    min_severity: int | None = None,
    max_severity: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Search symptoms by name, severity range, and date range.

    Filters are combined with AND logic. Name matching is case-insensitive.
    """
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if name is not None:
        conditions.append(f"name ILIKE ${idx}")
        params.append(name)
        idx += 1

    if min_severity is not None:
        conditions.append(f"severity >= ${idx}")
        params.append(min_severity)
        idx += 1

    if max_severity is not None:
        conditions.append(f"severity <= ${idx}")
        params.append(max_severity)
        idx += 1

    if start_date is not None:
        conditions.append(f"occurred_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"occurred_at <= ${idx}")
        params.append(_normalize_end_date(end_date))
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(
        f"SELECT * FROM symptoms {where} ORDER BY occurred_at DESC",
        *params,
    )
    return [_row_to_dict(r) for r in rows]
