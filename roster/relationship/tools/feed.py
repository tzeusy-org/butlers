"""Activity feed — log activities and retrieve feed entries backed by SPO facts.

Each activity entry is a temporal fact in the facts table:
  subject   = contact:{contact_id}
  predicate = 'activity'
  content   = description
  metadata  = {type, entity_type, entity_id}
  valid_at  = created_at  (temporal — each entry coexists independently)
  scope     = 'relationship'
  entity_id = contact's entity UUID (resolved via contacts.entity_id)

The response shape is backward compatible with the legacy activity_feed table:
  {id, contact_id, type, description, action, summary, entity_type, entity_id, created_at}
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

logger = logging.getLogger(__name__)

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _fact_to_feed_entry(row: dict[str, Any], contact_id: uuid.UUID) -> dict[str, Any]:
    """Convert a facts row to the activity feed API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    activity_type = meta.get("type", "")
    description = row.get("content", "")
    entity_type = meta.get("entity_type")
    entity_id_str = meta.get("entity_id")
    entity_id = uuid.UUID(entity_id_str) if entity_id_str else None
    return {
        "id": row["id"],
        "contact_id": contact_id,
        # Both legacy naming conventions supported:
        "type": activity_type,
        "description": description,
        "action": activity_type,
        "summary": description,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "created_at": row.get("valid_at") or row.get("created_at"),
    }


async def _log_activity(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str,
    description: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> None:
    """Log an activity entry as a temporal fact."""
    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)
    contact_entity_id = await resolve_contact_entity_id(pool, contact_id)
    embedding_engine = _get_embedding_engine()

    fact_metadata: dict[str, Any] = {"type": type}
    if entity_type is not None:
        fact_metadata["entity_type"] = entity_type
    if entity_id is not None:
        fact_metadata["entity_id"] = str(entity_id)

    await store_fact(
        pool,
        subject=f"contact:{contact_id}",
        predicate="activity",
        content=description,
        embedding_engine=embedding_engine,
        permanence="stable",
        scope="relationship",
        entity_id=contact_entity_id,
        valid_at=now,
        metadata=fact_metadata,
    )


async def feed_get(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get activity feed entries, optionally filtered by contact."""
    if contact_id is not None:
        rows = await pool.fetch(
            """
            SELECT id, content, valid_at, created_at, metadata,
                   subject
            FROM facts
            WHERE subject = $1
              AND predicate = 'activity'
              AND scope = 'relationship'
              AND validity = 'active'
            ORDER BY valid_at DESC
            LIMIT $2 OFFSET $3
            """,
            f"contact:{contact_id}",
            limit,
            offset,
        )
        return [_fact_to_feed_entry(dict(r), contact_id) for r in rows]

    # No contact filter — return all activity facts with contact_id resolved
    rows = await pool.fetch(
        """
        SELECT f.id, f.content, f.valid_at, f.created_at, f.metadata, f.subject,
               c.id AS _contact_id
        FROM facts f
        JOIN contacts c ON f.subject = 'contact:' || c.id::text
        WHERE f.predicate = 'activity'
          AND f.scope = 'relationship'
          AND f.validity = 'active'
        ORDER BY f.valid_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )
    return [_fact_to_feed_entry(dict(r), r["_contact_id"]) for r in rows]
