"""Health research — save, search, and summarize research notes backed by SPO facts."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


async def _get_owner_entity_id(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity's id from shared.entities."""
    try:
        row = await pool.fetchrow(
            "SELECT id FROM shared.entities WHERE 'owner' = ANY(roles) LIMIT 1"
        )
        return row["id"] if row else None
    except asyncpg.PostgresError:
        logger.debug("_get_owner_entity_id: shared.entities query failed", exc_info=True)
        return None


async def _validate_condition_fact(pool: asyncpg.Pool, condition_id: str) -> None:
    """Raise ValueError if no condition fact with this id exists."""
    cond_uuid = uuid.UUID(condition_id) if isinstance(condition_id, str) else condition_id
    row = await pool.fetchrow(
        "SELECT id FROM facts WHERE id = $1 AND predicate = 'condition' AND scope = 'health'",
        cond_uuid,
    )
    if row is None:
        raise ValueError(f"Condition {condition_id} not found")


def _fact_to_research(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the research API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    cond_id = meta.get("condition_id")
    cond_uuid = uuid.UUID(cond_id) if cond_id else None
    return {
        "id": row["id"],  # UUID — matches old DB row behaviour
        "title": meta.get("title", ""),
        "content": row.get("content", ""),
        "tags": meta.get("tags", []),
        "source_url": meta.get("source_url"),
        "condition_id": cond_uuid,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }


async def research_save(
    pool: asyncpg.Pool,
    title: str,
    content: str,
    tags: list[str] | None = None,
    source_url: str | None = None,
    condition_id: str | None = None,
) -> dict[str, Any]:
    """Save a research note with optional tags, source URL, and condition link."""
    if condition_id is not None:
        await _validate_condition_fact(pool, condition_id)

    from butlers.modules.memory.storage import store_fact

    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)

    metadata: dict[str, Any] = {
        "title": title,
        "tags": tags or [],
    }
    if source_url is not None:
        metadata["source_url"] = source_url
    if condition_id is not None:
        metadata["condition_id"] = str(condition_id)

    # Use title-keyed subject so multiple research notes coexist independently.
    subject = f"research:{title}"

    fact_id = (
        await store_fact(
            pool,
            subject=subject,
            predicate="research",
            content=content,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="health",
            entity_id=await _get_owner_entity_id(pool),
            valid_at=None,  # property fact — supersedes previous for same title
            metadata=metadata,
        )
    )["id"]

    return {
        "id": fact_id,
        "title": title,
        "content": content,
        "tags": tags or [],
        "source_url": source_url,
        "condition_id": uuid.UUID(condition_id) if condition_id else None,
        "created_at": now,
        "updated_at": now,
    }


async def research_search(
    pool: asyncpg.Pool,
    query: str | None = None,
    tags: list[str] | None = None,
    condition_id: str | None = None,
) -> list[dict[str, Any]]:
    """Search research notes by text query, tags, and/or condition.

    Filters are combined with AND logic. Query performs case-insensitive search
    against title (metadata) and content. Tags matches entries containing any of
    the given tags. condition_id matches entries with that condition fact id.
    """
    conditions: list[str] = [
        "predicate = 'research'",
        "validity = 'active'",
        "scope = 'health'",
    ]
    params: list[Any] = []
    idx = 1

    if query is not None:
        pattern = f"%{query}%"
        conditions.append(f"(content ILIKE ${idx} OR metadata->>'title' ILIKE ${idx})")
        params.append(pattern)
        idx += 1

    if tags is not None:
        # Check if metadata->'tags' contains any of the provided tags
        conditions.append(f"metadata->'tags' ?| ${idx}")
        params.append(tags)
        idx += 1

    if condition_id is not None:
        conditions.append(f"metadata->>'condition_id' = ${idx}")
        params.append(str(condition_id))
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT id, predicate, content, valid_at, created_at, metadata"
        f" FROM facts {where} ORDER BY created_at DESC",
        *params,
    )
    return [_fact_to_research(dict(r)) for r in rows]


async def research_summarize(
    pool: asyncpg.Pool,
    condition_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Summarize research entries, optionally scoped by condition or tags.

    Returns count, unique tags across matches, and titles of included articles.
    """
    conditions: list[str] = [
        "predicate = 'research'",
        "validity = 'active'",
        "scope = 'health'",
    ]
    params: list[Any] = []
    idx = 1

    if condition_id is not None:
        conditions.append(f"metadata->>'condition_id' = ${idx}")
        params.append(str(condition_id))
        idx += 1

    if tags is not None:
        conditions.append(f"metadata->'tags' ?| ${idx}")
        params.append(tags)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT metadata->>'title' AS title, metadata->'tags' AS tags"
        f" FROM facts {where} ORDER BY created_at DESC",
        *params,
    )

    titles: list[str] = []
    all_tags: set[str] = set()

    for row in rows:
        title = row["title"]
        if title:
            titles.append(title)
        row_tags = row["tags"]
        if isinstance(row_tags, str):
            row_tags = json.loads(row_tags)
        if isinstance(row_tags, list):
            all_tags.update(row_tags)

    return {
        "count": len(rows),
        "tags": sorted(all_tags),
        "titles": titles,
    }
