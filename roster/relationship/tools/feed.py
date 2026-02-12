"""Activity feed — log activities and retrieve feed entries."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import has_table, table_columns


async def _log_activity(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str,
    description: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> None:
    """Log an activity to whichever feed table is available."""
    if await has_table(pool, "activity_feed"):
        cols = await table_columns(pool, "activity_feed")
        if {"contact_id", "type", "description"}.issubset(cols):
            if {"entity_type", "entity_id"}.issubset(cols):
                await pool.execute(
                    """
                    INSERT INTO activity_feed (
                        contact_id, type, description, entity_type, entity_id
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    contact_id,
                    type,
                    description,
                    entity_type,
                    entity_id,
                )
                return
            await pool.execute(
                """
                INSERT INTO activity_feed (contact_id, type, description)
                VALUES ($1, $2, $3)
                """,
                contact_id,
                type,
                description,
            )
            return
        if {"contact_id", "action", "summary"}.issubset(cols):
            if {"entity_type", "entity_id"}.issubset(cols):
                await pool.execute(
                    """
                    INSERT INTO activity_feed (contact_id, action, summary, entity_type, entity_id)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    contact_id,
                    type,
                    description,
                    entity_type,
                    entity_id,
                )
                return
            await pool.execute(
                """
                INSERT INTO activity_feed (contact_id, action, summary)
                VALUES ($1, $2, $3)
                """,
                contact_id,
                type,
                description,
            )
            return

    if await has_table(pool, "contact_feed"):
        cols = await table_columns(pool, "contact_feed")
        if {"contact_id", "action", "summary"}.issubset(cols):
            if {"entity_type", "entity_id"}.issubset(cols):
                await pool.execute(
                    """
                    INSERT INTO contact_feed (contact_id, action, summary, entity_type, entity_id)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    contact_id,
                    type,
                    description,
                    entity_type,
                    entity_id,
                )
                return
            await pool.execute(
                """
                INSERT INTO contact_feed (contact_id, action, summary)
                VALUES ($1, $2, $3)
                """,
                contact_id,
                type,
                description,
            )
            return

    raise ValueError("No supported feed table found (expected activity_feed or contact_feed)")


async def feed_get(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get activity feed entries, optionally filtered by contact."""
    table = "activity_feed" if await has_table(pool, "activity_feed") else "contact_feed"
    cols = await table_columns(pool, table)

    if contact_id is not None:
        rows = await pool.fetch(
            f"""
            SELECT * FROM {table}
            WHERE contact_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            contact_id,
            limit,
            offset,
        )
    else:
        rows = await pool.fetch(
            f"""
            SELECT * FROM {table}
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )

    results: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        if "type" not in cols and "action" in cols:
            entry["type"] = entry.get("action")
        if "description" not in cols and "summary" in cols:
            entry["description"] = entry.get("summary")
        results.append(entry)
    return results
