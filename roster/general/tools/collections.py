"""Collection management — create, list, delete, and export collections."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def collection_create(
    pool: asyncpg.Pool, name: str, description: str | None = None
) -> uuid.UUID:
    """Create a new collection."""
    collection_id = await pool.fetchval(
        "INSERT INTO collections (name, description) VALUES ($1, $2) RETURNING id",
        name,
        description,
    )
    return collection_id


async def collection_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all collections."""
    rows = await pool.fetch("SELECT * FROM collections ORDER BY name")
    return [dict(row) for row in rows]


async def collection_delete(pool: asyncpg.Pool, collection_id: uuid.UUID) -> None:
    """Delete a collection and all its items (CASCADE)."""
    result = await pool.execute("DELETE FROM collections WHERE id = $1", collection_id)
    if result == "DELETE 0":
        raise ValueError(f"Collection {collection_id} not found")


async def collection_export(pool: asyncpg.Pool, collection_name: str) -> list[dict[str, Any]]:
    """Export all items from a collection as a list of dicts."""
    rows = await pool.fetch(
        """
        SELECT e.id, e.data, e.tags, e.created_at, e.updated_at
        FROM collection_items e
        JOIN collections c ON e.collection_id = c.id
        WHERE c.name = $1
        ORDER BY e.created_at
        """,
        collection_name,
    )

    result = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("data"), str):
            d["data"] = json.loads(d["data"])
        if isinstance(d.get("tags"), str):
            d["tags"] = json.loads(d["tags"])
        result.append(d)
    return result
