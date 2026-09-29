"""Collection item management — create, read, update, delete, and search items.

Every path filters to ordinary parents through
:mod:`butlers.tools.general.source_authority`: items under a private (or
inconsistently classified) collection behave exactly like absent items, and
every mutation records an immutable source version in its own transaction.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from butlers.tools.general._helpers import _deep_merge
from butlers.tools.general.source_authority import (
    ORDINARY_PARENT,
    GeneralSourceUnavailable,
    SourceVersion,
    lock_ordinary_item,
    lock_ordinary_parent,
    ordinary_transaction,
    record_source_version,
    reject_reserved_fields,
    require_ordinary_namespace,
)

logger = logging.getLogger(__name__)


def _not_found(item_id: uuid.UUID) -> ValueError:
    return ValueError(
        f"Item {item_id} not found. "
        "Use item_search(collection=<name>, query=...) to find existing items."
    )


async def resolve_ordinary_collection(
    conn: asyncpg.Connection, collection_name: str, *, create: bool
) -> asyncpg.Record | None:
    """Resolve a name in the ordinary namespace and share-lock its parent.

    Order: an ordinary collection with exactly this name, then an exactly
    declared ordinary alias.  With ``create``, an absent name creates a new
    ordinary collection -- a name used only by a private collection is absent
    here.  Writers use a targetless ``ON CONFLICT DO NOTHING`` and a locked
    ordinary reread, valid with or without the legacy global constraint.
    The caller must already have passed :func:`require_ordinary_namespace`.
    """
    lookup = f"""
        SELECT id FROM (
            SELECT c.id, 0 AS precedence
            FROM collections AS c
            WHERE c.name = $1 AND {ORDINARY_PARENT}
            UNION ALL
            SELECT key_row.collection_id, 1
            FROM collection_vocabulary_keys AS key_row
            JOIN collections AS c ON c.id = key_row.collection_id
            WHERE key_row.spelling = $1 AND NOT key_row.custody_private AND {ORDINARY_PARENT}
        ) AS candidate
        ORDER BY precedence
        LIMIT 1
    """
    for _ in range(2):
        collection_id = await conn.fetchval(lookup, collection_name)
        if collection_id is not None:
            parent = await lock_ordinary_parent(conn, collection_id)
            if parent is not None:
                return parent
            # Classified while we waited: re-resolve in the ordinary namespace.
            continue
        if not create:
            return None
        collection_id = await conn.fetchval(
            "INSERT INTO collections (name) VALUES ($1) ON CONFLICT DO NOTHING RETURNING id",
            collection_name,
        )
        if collection_id is not None:
            return await lock_ordinary_parent(conn, collection_id)
        # A concurrent ordinary create won (reread finds it), or the name is
        # held by a row the ordinary namespace cannot see (refuse uniformly).
    raise GeneralSourceUnavailable()


async def item_create_versioned(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    collection_name: str,
    data: dict[str, Any],
    tags: list[str] | None = None,
) -> tuple[uuid.UUID, SourceVersion]:
    """Create an ordinary item and its first source version atomically.

    Accepts a pool or an acquired connection so a caller can compose the item,
    its version and its own receipt in one General transaction.
    """
    reject_reserved_fields(data)
    tags_value = list(tags) if tags is not None else []
    async with ordinary_transaction(pool_or_conn) as conn:
        await require_ordinary_namespace(conn)
        parent = await resolve_ordinary_collection(conn, collection_name, create=True)
        item_id = await conn.fetchval(
            """INSERT INTO collection_items (collection_id, data, tags)
               VALUES ($1, $2, $3)
               RETURNING id""",
            parent["id"],
            data,
            tags_value,
        )
        version = await record_source_version(conn, parent, item_id, "create")
    return item_id, version


async def item_create(
    pool: asyncpg.Pool,
    collection_name: str,
    data: dict[str, Any],
    tags: list[str] | None = None,
) -> uuid.UUID:
    """Create an item in a collection, creating the collection if needed."""
    item_id, _ = await item_create_versioned(pool, collection_name, data, tags=tags)
    return item_id


def _decode_row(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    if isinstance(d.get("data"), str):
        d["data"] = json.loads(d["data"])
    if isinstance(d.get("tags"), str):
        d["tags"] = json.loads(d["tags"])
    return d


async def item_get(pool: asyncpg.Pool, item_id: uuid.UUID) -> dict[str, Any] | None:
    """Get an item by ID."""
    async with ordinary_transaction(pool) as conn:
        row = await conn.fetchrow(
            f"""
            SELECT e.id, e.collection_id, e.data, e.tags, e.created_at, e.updated_at
            FROM collection_items AS e
            JOIN collections AS c ON c.id = e.collection_id
            WHERE e.id = $1 AND {ORDINARY_PARENT}
            """,
            item_id,
        )
    if row is None:
        return None
    return _decode_row(row)


async def item_update(
    pool: asyncpg.Pool,
    item_id: uuid.UUID,
    data: dict[str, Any],
    tags: list[str] | None = None,
) -> None:
    """Update an item with deep merge for data, full replace for tags.

    Locks the parent then the item, merges under that lock, and records the
    new source version in the same transaction.  If tags is provided, it fully
    replaces the existing tags array.
    """
    reject_reserved_fields(data)
    async with ordinary_transaction(pool) as conn:
        locked = await lock_ordinary_item(conn, item_id)
        if locked is None:
            raise _not_found(item_id)
        parent, row = locked

        existing = row["data"]
        if isinstance(existing, str):
            existing = json.loads(existing)
        merged = _deep_merge(existing, data)

        if tags is not None:
            await conn.execute(
                """UPDATE collection_items
                   SET data = $2, tags = $3, updated_at = now()
                   WHERE id = $1""",
                item_id,
                merged,
                list(tags),
            )
        else:
            await conn.execute(
                "UPDATE collection_items SET data = $2, updated_at = now() WHERE id = $1",
                item_id,
                merged,
            )
        await record_source_version(conn, parent, item_id, "update")


async def item_search(
    pool: asyncpg.Pool,
    collection_name: str | None = None,
    query: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search ordinary items using JSONB containment (@>).

    Optionally filter by collection name, JSONB query, and/or tags.
    Tag filtering uses JSONB containment: each tag must be present in the tags array.
    Uses the GIN indexes on collection_items.data and collection_items.tags.
    """
    conditions: list[str] = [ORDINARY_PARENT]
    params: list[Any] = []
    idx = 1

    if collection_name:
        conditions.append(f"c.name = ${idx}")
        params.append(collection_name)
        idx += 1

    if query:
        conditions.append(f"e.data @> ${idx}")
        params.append(query)
        idx += 1

    if tags:
        for tag in tags:
            conditions.append(f"e.tags @> ${idx}")
            params.append([tag])
            idx += 1

    async with ordinary_transaction(pool) as conn:
        if collection_name:
            await require_ordinary_namespace(conn)
        rows = await conn.fetch(
            f"""
            SELECT e.id, e.collection_id, e.data, e.tags, e.created_at, e.updated_at,
                   c.name as collection_name
            FROM collection_items e
            JOIN collections c ON e.collection_id = c.id
            WHERE {" AND ".join(conditions)}
            ORDER BY e.created_at DESC
            """,
            *params,
        )
    return [_decode_row(row) for row in rows]


async def item_delete(pool: asyncpg.Pool, item_id: uuid.UUID) -> None:
    """Delete an ordinary item, recording its tombstone source version first."""
    async with ordinary_transaction(pool) as conn:
        locked = await lock_ordinary_item(conn, item_id)
        if locked is None:
            raise _not_found(item_id)
        parent, _ = locked
        await record_source_version(conn, parent, item_id, "delete")
        await conn.execute("DELETE FROM collection_items WHERE id = $1", item_id)
