"""Interactions — log and list interactions with contacts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import table_columns
from butlers.tools.relationship.feed import _log_activity

_VALID_DIRECTIONS = ("incoming", "outgoing", "mutual")


async def interaction_log(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str,
    summary: str | None = None,
    occurred_at: datetime | None = None,
    direction: str | None = None,
    duration_minutes: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Log an interaction with a contact."""
    if direction is not None and direction not in _VALID_DIRECTIONS:
        raise ValueError(f"Invalid direction '{direction}'. Must be one of {_VALID_DIRECTIONS}")
    cols = await table_columns(pool, "interactions")
    effective_occurred_at = occurred_at if occurred_at is not None else datetime.now()

    # Idempotency guard: same contact + type + date skips duplicate insert.
    if "occurred_at" in cols:
        existing = await pool.fetchrow(
            """
            SELECT id
            FROM interactions
            WHERE contact_id = $1
              AND type = $2
              AND occurred_at::date = $3::date
            LIMIT 1
            """,
            contact_id,
            type,
            effective_occurred_at,
        )
        if existing is not None:
            return {
                "skipped": "duplicate",
                "existing_id": str(existing["id"]),
            }

    insert_cols: list[str] = []
    values: list[Any] = []

    def add(col: str, val: Any) -> None:
        if col in cols:
            insert_cols.append(col)
            values.append(val)

    add("contact_id", contact_id)
    add("type", type)
    add("summary", summary)
    add("occurred_at", effective_occurred_at)
    add("direction", direction)
    add("duration_minutes", duration_minutes)
    add("metadata", json.dumps(metadata) if metadata is not None else None)

    placeholders: list[str] = []
    for idx, col in enumerate(insert_cols, start=1):
        if col == "metadata":
            placeholders.append(f"${idx}::jsonb")
        else:
            placeholders.append(f"${idx}")

    row = await pool.fetchrow(
        f"""
        INSERT INTO interactions ({", ".join(insert_cols)})
        VALUES ({", ".join(placeholders)})
        RETURNING *
        """,
        *values,
    )
    result = dict(row)
    if isinstance(result.get("metadata"), str):
        result["metadata"] = json.loads(result["metadata"])
    desc = f"Logged '{type}' interaction"
    if direction:
        desc += f" ({direction})"
    await _log_activity(pool, contact_id, "interaction_logged", desc)
    return result


async def interaction_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    limit: int = 20,
    direction: str | None = None,
    type: str | None = None,
) -> list[dict[str, Any]]:
    """List interactions for a contact, most recent first.

    Optionally filter by direction and/or type.
    """
    cols = await table_columns(pool, "interactions")
    conditions = ["contact_id = $1"]
    params: list[Any] = [contact_id]
    idx = 2

    if direction is not None and "direction" in cols:
        conditions.append(f"direction = ${idx}")
        params.append(direction)
        idx += 1

    if type is not None:
        conditions.append(f"type = ${idx}")
        params.append(type)
        idx += 1

    where = " AND ".join(conditions)
    query = f"""
        SELECT * FROM interactions
        WHERE {where}
        ORDER BY occurred_at DESC
        LIMIT ${idx}
    """
    params.append(limit)

    rows = await pool.fetch(query, *params)
    results = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("metadata"), str):
            d["metadata"] = json.loads(d["metadata"])
        results.append(d)
    return results
