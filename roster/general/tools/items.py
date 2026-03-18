"""Collection item management — create, read, update, delete, and search items."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from butlers.tools.general._helpers import _deep_merge

logger = logging.getLogger(__name__)


async def item_create(
    pool: asyncpg.Pool,
    collection_name: str,
    data: dict[str, Any],
    tags: list[str] | None = None,
) -> uuid.UUID:
    """Create an item in a collection (by collection name).

    Raises ValueError if collection not found.
    """
    collection_id = await pool.fetchval(
        "SELECT id FROM collections WHERE name = $1", collection_name
    )
    if collection_id is None:
        raise ValueError(f"Collection '{collection_name}' not found")

    tags_json = json.dumps(tags if tags is not None else [])
    item_id = await pool.fetchval(
        """INSERT INTO collection_items (collection_id, data, tags)
           VALUES ($1, $2::jsonb, $3::jsonb)
           RETURNING id""",
        collection_id,
        json.dumps(data),
        tags_json,
    )
    return item_id


async def item_get(pool: asyncpg.Pool, item_id: uuid.UUID) -> dict[str, Any] | None:
    """Get an item by ID."""
    row = await pool.fetchrow("SELECT * FROM collection_items WHERE id = $1", item_id)
    if row is None:
        return None
    d = dict(row)
    if isinstance(d.get("data"), str):
        d["data"] = json.loads(d["data"])
    if isinstance(d.get("tags"), str):
        d["tags"] = json.loads(d["tags"])
    return d


async def item_update(
    pool: asyncpg.Pool,
    item_id: uuid.UUID,
    data: dict[str, Any],
    tags: list[str] | None = None,
) -> None:
    """Update an item with deep merge for data, full replace for tags.

    Fetches current data, deep merges in Python, then writes back.
    If tags is provided, it fully replaces the existing tags array.
    This is safe since items have per-row granularity.
    """
    row = await pool.fetchrow("SELECT data FROM collection_items WHERE id = $1", item_id)
    if row is None:
        raise ValueError(f"Item {item_id} not found")

    existing = row["data"]
    if isinstance(existing, str):
        existing = json.loads(existing)

    merged = _deep_merge(existing, data)

    if tags is not None:
        tags_json = json.dumps(tags)
        await pool.execute(
            """UPDATE collection_items
               SET data = $2::jsonb, tags = $3::jsonb, updated_at = now()
               WHERE id = $1""",
            item_id,
            json.dumps(merged),
            tags_json,
        )
    else:
        await pool.execute(
            "UPDATE collection_items SET data = $2::jsonb, updated_at = now() WHERE id = $1",
            item_id,
            json.dumps(merged),
        )


async def item_search(
    pool: asyncpg.Pool,
    collection_name: str | None = None,
    query: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search items using JSONB containment (@>).

    Optionally filter by collection name, JSONB query, and/or tags.
    Tag filtering uses JSONB containment: each tag must be present in the tags array.
    Uses the GIN indexes on collection_items.data and collection_items.tags.
    """
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if collection_name:
        conditions.append(f"c.name = ${idx}")
        params.append(collection_name)
        idx += 1

    if query:
        conditions.append(f"e.data @> ${idx}::jsonb")
        params.append(json.dumps(query))
        idx += 1

    if tags:
        for tag in tags:
            conditions.append(f"e.tags @> ${idx}::jsonb")
            params.append(json.dumps([tag]))
            idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    rows = await pool.fetch(
        f"""
        SELECT e.id, e.collection_id, e.data, e.tags, e.created_at, e.updated_at,
               c.name as collection_name
        FROM collection_items e
        JOIN collections c ON e.collection_id = c.id
        {where}
        ORDER BY e.created_at DESC
        """,
        *params,
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


async def item_delete(pool: asyncpg.Pool, item_id: uuid.UUID) -> None:
    """Delete an item."""
    result = await pool.execute("DELETE FROM collection_items WHERE id = $1", item_id)
    if result == "DELETE 0":
        raise ValueError(f"Item {item_id} not found")
