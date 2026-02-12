"""Interactions — log and list interactions with contacts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg

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
    row = await pool.fetchrow(
        """
        INSERT INTO interactions (contact_id, type, summary, occurred_at,
                                  direction, duration_minutes, metadata)
        VALUES ($1, $2, $3, COALESCE($4, now()), $5, $6, $7::jsonb)
        RETURNING *
        """,
        contact_id,
        type,
        summary,
        occurred_at,
        direction,
        duration_minutes,
        json.dumps(metadata) if metadata is not None else None,
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
    conditions = ["contact_id = $1"]
    params: list[Any] = [contact_id]
    idx = 2

    if direction is not None:
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
