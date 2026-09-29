"""Collection management — create, list, delete, and export collections.

All paths operate in the ordinary namespace only (see
:mod:`butlers.tools.general.source_authority`): a private or inconsistently
classified collection is omitted from lists and exports and behaves exactly
like an absent collection for create and delete.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from butlers.tools.general.source_authority import (
    ORDINARY_PARENT,
    GeneralSourceUnavailable,
    lock_ordinary_parent,
    ordinary_transaction,
    record_source_version,
    require_ordinary_namespace,
)

logger = logging.getLogger(__name__)


async def collection_create(
    pool: asyncpg.Pool, name: str, description: str | None = None
) -> uuid.UUID:
    """Create a new collection.

    Duplicate ordinary names still raise ``UniqueViolationError``; a name used
    only by a private collection is absent here and creates a new ordinary one.
    """
    async with ordinary_transaction(pool) as conn:
        await require_ordinary_namespace(conn)
        collection_id = await conn.fetchval(
            """
            INSERT INTO collections (name, description) VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            name,
            description,
        )
        if collection_id is not None:
            return collection_id
        existing = await conn.fetchval(
            f"SELECT c.id FROM collections AS c WHERE c.name = $1 AND {ORDINARY_PARENT} "
            "FOR SHARE OF c",
            name,
        )
        if existing is None:
            raise GeneralSourceUnavailable()
    raise asyncpg.UniqueViolationError(
        "duplicate key value violates unique constraint on ordinary collection name"
    )


async def collection_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all ordinary collections."""
    async with ordinary_transaction(pool) as conn:
        rows = await conn.fetch(
            f"""
            SELECT c.id, c.name, c.description, c.created_at
            FROM collections AS c
            WHERE {ORDINARY_PARENT}
            ORDER BY c.name
            """
        )
    return [dict(row) for row in rows]


async def collection_delete(pool: asyncpg.Pool, collection_id: uuid.UUID) -> None:
    """Delete an ordinary collection and all its items (CASCADE).

    Locks the parent exclusively, then its items, and records a tombstone
    source version for every item before the cascade removes it.
    """
    async with ordinary_transaction(pool) as conn:
        parent = await lock_ordinary_parent(conn, collection_id, exclusive=True)
        if parent is None:
            raise ValueError(
                f"Collection {collection_id} not found. "
                "Use collection_list() to see available collections."
            )
        item_ids = await conn.fetch(
            "SELECT id FROM collection_items WHERE collection_id = $1 ORDER BY id FOR UPDATE",
            collection_id,
        )
        for row in item_ids:
            await record_source_version(conn, parent, row["id"], "delete")
        await conn.execute("DELETE FROM collections WHERE id = $1", collection_id)


async def collection_export(pool: asyncpg.Pool, collection_name: str) -> list[dict[str, Any]]:
    """Export all items from an ordinary collection as a list of dicts."""
    async with ordinary_transaction(pool) as conn:
        await require_ordinary_namespace(conn)
        rows = await conn.fetch(
            f"""
            SELECT e.id, e.data, e.tags, e.created_at, e.updated_at
            FROM collection_items e
            JOIN collections c ON e.collection_id = c.id
            WHERE c.name = $1 AND {ORDINARY_PARENT}
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
