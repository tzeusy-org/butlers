"""Interactions — log and list interactions with contacts backed by SPO facts.

Each interaction is a temporal fact in the facts table:
  subject   = contact:{contact_id}
  predicate = 'interaction'
  content   = summary
  metadata  = {type, direction, duration_minutes, extra_metadata}
  valid_at  = occurred_at   (temporal — multiple coexist per contact)
  scope     = 'relationship'
  entity_id = contact's entity UUID (resolved via contacts.entity_id)

The response shape is backward compatible with the legacy interactions table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id
from butlers.tools.relationship.feed import _log_activity

logger = logging.getLogger(__name__)

_VALID_DIRECTIONS = ("incoming", "outgoing", "mutual")

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _fact_to_interaction(row: dict[str, Any], contact_id: uuid.UUID) -> dict[str, Any]:
    """Convert a facts row to the interactions API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return {
        "id": row["id"],
        "contact_id": contact_id,
        "type": meta.get("type", ""),
        "summary": row.get("content") or None,
        "occurred_at": row.get("valid_at"),
        "created_at": row.get("created_at"),
        "direction": meta.get("direction"),
        "duration_minutes": meta.get("duration_minutes"),
        "metadata": meta.get("extra_metadata"),
    }


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

    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)
    effective_occurred_at = occurred_at if occurred_at is not None else now

    # Idempotency guard: only explicit timestamps are treated as deterministic backfills.
    if occurred_at is not None:
        existing = await pool.fetchrow(
            """
            SELECT id FROM facts
            WHERE subject = $1
              AND predicate = 'interaction'
              AND scope = 'relationship'
              AND validity = 'active'
              AND valid_at::date = $2::date
              AND metadata->>'type' = $3
            LIMIT 1
            """,
            f"contact:{contact_id}",
            occurred_at,
            type,
        )
        if existing is not None:
            return {
                "skipped": "duplicate",
                "existing_id": str(existing["id"]),
            }

    entity_id = await resolve_contact_entity_id(pool, contact_id)
    embedding_engine = _get_embedding_engine()

    fact_metadata: dict[str, Any] = {"type": type}
    if direction is not None:
        fact_metadata["direction"] = direction
    if duration_minutes is not None:
        fact_metadata["duration_minutes"] = duration_minutes
    if metadata is not None:
        fact_metadata["extra_metadata"] = metadata

    fact_id = await store_fact(
        pool,
        subject=f"contact:{contact_id}",
        predicate="interaction",
        content=summary or "",
        embedding_engine=embedding_engine,
        permanence="stable",
        scope="relationship",
        entity_id=entity_id,
        valid_at=effective_occurred_at,
        metadata=fact_metadata,
    )

    result = {
        "id": fact_id,
        "contact_id": contact_id,
        "type": type,
        "summary": summary,
        "occurred_at": effective_occurred_at,
        "created_at": now,
        "direction": direction,
        "duration_minutes": duration_minutes,
        "metadata": metadata,
    }

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
    conditions = [
        "subject = $1",
        "predicate = 'interaction'",
        "scope = 'relationship'",
        "validity = 'active'",
    ]
    params: list[Any] = [f"contact:{contact_id}"]
    idx = 2

    if direction is not None:
        conditions.append(f"metadata->>'direction' = ${idx}")
        params.append(direction)
        idx += 1

    if type is not None:
        conditions.append(f"metadata->>'type' = ${idx}")
        params.append(type)
        idx += 1

    params.append(limit)
    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT id, content, valid_at, created_at, metadata
        FROM facts
        WHERE {where}
        ORDER BY valid_at DESC
        LIMIT ${idx}
        """,
        *params,
    )
    return [_fact_to_interaction(dict(r), contact_id) for r in rows]
