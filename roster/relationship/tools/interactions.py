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
    """Log an interaction with a contact. Skips duplicate contact+type+date."""
    cols = await table_columns(pool, "interactions")
    has_direction = "direction" in cols

    if has_direction and direction is not None and direction not in _VALID_DIRECTIONS:
        raise ValueError(f"Invalid direction '{direction}'. Must be one of {_VALID_DIRECTIONS}")

    # Idempotency guard is only applied when caller supplies an explicit occurred_at.
    # This preserves ingestion idempotency while allowing day-to-day ad-hoc logs.
    if occurred_at is not None:
        existing = await pool.fetchrow(
            """
            SELECT id FROM interactions
            WHERE contact_id = $1 AND type = $2 AND DATE(occurred_at) = DATE($3)
            """,
            contact_id,
            type,
            occurred_at,
        )
        if existing is not None:
            return {"skipped": "duplicate", "existing_id": str(existing["id"])}
    insert_cols: list[str] = ["contact_id", "type"]
    values: list[Any] = [contact_id, type]
    placeholders: list[str] = ["$1", "$2"]

    if "summary" in cols:
        insert_cols.append("summary")
        values.append(summary)
        placeholders.append(f"${len(values)}")

    if "occurred_at" in cols and occurred_at is not None:
        insert_cols.append("occurred_at")
        values.append(occurred_at)
        placeholders.append(f"${len(values)}")

    if has_direction and direction is not None:
        insert_cols.append("direction")
        values.append(direction)
        placeholders.append(f"${len(values)}")

    if "duration_minutes" in cols and duration_minutes is not None:
        insert_cols.append("duration_minutes")
        values.append(duration_minutes)
        placeholders.append(f"${len(values)}")

    if "metadata" in cols and metadata is not None:
        insert_cols.append("metadata")
        values.append(json.dumps(metadata))
        placeholders.append(f"${len(values)}::jsonb")

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
    order_col = "occurred_at" if "occurred_at" in cols else "created_at"
    query = f"""
        SELECT * FROM interactions
        WHERE {where}
        ORDER BY {order_col} DESC
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
