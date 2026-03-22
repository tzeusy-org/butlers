"""Life events — log and list significant life events for contacts backed by SPO facts.

Each life event is a temporal fact in the facts table:
  subject   = contact:{contact_id}
  predicate = 'life_event'
  content   = summary
  metadata  = {life_event_type, description}
  valid_at  = happened_at  (temporal — multiple events coexist per contact)
  scope     = 'relationship'
  entity_id = contact's entity UUID (resolved via contacts.entity_id)

The response shape is backward compatible with the legacy life_events table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id
from butlers.tools.relationship.feed import _log_activity

logger = logging.getLogger(__name__)

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _fact_to_life_event(row: dict[str, Any], contact_id: uuid.UUID) -> dict[str, Any]:
    """Convert a facts row to the life events API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    # Convert valid_at (TIMESTAMPTZ) to date for backward compat with legacy happened_at (DATE).
    valid_at = row.get("valid_at")
    if valid_at is not None and isinstance(valid_at, datetime):
        happened_at: date | None = valid_at.date()
    elif valid_at is not None and isinstance(valid_at, date):
        happened_at = valid_at
    else:
        happened_at = None
    return {
        "id": row["id"],
        "contact_id": contact_id,
        "type_name": meta.get("life_event_type", ""),
        "summary": row.get("content", ""),
        "description": meta.get("description"),
        "happened_at": happened_at,
        "created_at": row.get("created_at"),
    }


async def life_event_types_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all available life event types with their categories.

    Falls back to a static list when the life_event_types taxonomy table is absent.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT t.id, t.name, c.name as category
            FROM life_event_types t
            JOIN life_event_categories c ON t.category_id = c.id
            ORDER BY c.name, t.name
            """
        )
        if rows:
            return [dict(row) for row in rows]
    except asyncpg.PostgresError:
        pass

    # Fallback: common life event types derived from the data we see in facts
    rows = await pool.fetch(
        """
        SELECT DISTINCT metadata->>'life_event_type' AS name
        FROM facts
        WHERE predicate = 'life_event'
          AND scope = 'relationship'
          AND metadata->>'life_event_type' IS NOT NULL
        ORDER BY name
        """
    )
    return [{"id": None, "name": r["name"], "category": "Unknown"} for r in rows]


async def life_event_log(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type_name: str,
    summary: str | None = None,
    description: str | None = None,
    happened_at: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Log a life event for a contact.

    Args:
        contact_id: UUID of the contact.
        type_name: Name of the life event type (e.g. 'promotion', 'married').
        summary: Short summary of the event.
        description: Optional longer description.
        happened_at: Optional date string (YYYY-MM-DD format).
        occurred_at: Optional datetime (alternative to happened_at).
    """
    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)

    # Validate type_name against the taxonomy table (or known static fallback types).
    known_types: set[str] = set()
    try:
        type_rows = await pool.fetch("SELECT name FROM life_event_types")
        known_types = {r["name"] for r in type_rows}
    except asyncpg.PostgresError:
        pass

    if not known_types:
        # Static fallback: the types seeded in the test fixture and default migration
        known_types = {
            "new job",
            "promotion",
            "quit",
            "retired",
            "graduated",
            "married",
            "divorced",
            "had a child",
            "moved",
            "passed away",
            "met for first time",
            "reconnected",
        }

    if type_name not in known_types:
        raise ValueError(
            f"Unknown life event type: '{type_name}'. Valid types: {sorted(known_types)}"
        )

    # Resolve happened_at timestamp
    if occurred_at is not None:
        valid_at = occurred_at
        happened_at_date: date | None = occurred_at.date()
    elif happened_at is not None:
        happened_at_date = date.fromisoformat(happened_at)
        valid_at = datetime.combine(
            happened_at_date,
            datetime.min.time(),
            tzinfo=UTC,
        )
    else:
        valid_at = now
        happened_at_date = None

    effective_summary = summary or description or type_name

    # Idempotency guard: same type+date for same contact
    existing = await pool.fetchrow(
        """
        SELECT id FROM facts
        WHERE subject = $1
          AND predicate = 'life_event'
          AND scope = 'relationship'
          AND validity = 'active'
          AND valid_at::date = $2::date
          AND metadata->>'life_event_type' = $3
        LIMIT 1
        """,
        f"contact:{contact_id}",
        valid_at,
        type_name,
    )
    if existing is not None:
        return {
            "skipped": "duplicate",
            "existing_id": str(existing["id"]),
        }

    entity_id = await resolve_contact_entity_id(pool, contact_id)
    embedding_engine = _get_embedding_engine()

    fact_metadata: dict[str, Any] = {"life_event_type": type_name}
    if description is not None:
        fact_metadata["description"] = description

    fact_id = (
        await store_fact(
            pool,
            subject=f"contact:{contact_id}",
            predicate="life_event",
            content=effective_summary,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="relationship",
            entity_id=entity_id,
            valid_at=valid_at,
            metadata=fact_metadata,
        )
    )["id"]

    activity_summary = f"Life event: {type_name} - {effective_summary}"
    await _log_activity(pool, contact_id, "life_event_logged", activity_summary)

    return {
        "id": fact_id,
        "contact_id": contact_id,
        "type_name": type_name,
        "summary": effective_summary,
        "description": description,
        "happened_at": happened_at_date,
        "created_at": now,
    }


async def life_event_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    type_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List life events, optionally filtered by contact and/or type."""
    # Base conditions — always unambiguous (no JOIN yet)
    base_conditions = [
        "predicate = 'life_event'",
        "scope = 'relationship'",
        "validity = 'active'",
    ]
    params: list[Any] = []
    idx = 1

    if contact_id is not None:
        base_conditions.append(f"subject = ${idx}")
        params.append(f"contact:{contact_id}")
        idx += 1

    if type_name is not None:
        base_conditions.append(f"metadata->>'life_event_type' = ${idx}")
        params.append(type_name)
        idx += 1

    params.append(limit)
    where_simple = " AND ".join(base_conditions)

    if contact_id is not None:
        rows = await pool.fetch(
            f"""
            SELECT id, content, valid_at, created_at, metadata
            FROM facts
            WHERE {where_simple}
            ORDER BY valid_at DESC NULLS LAST, created_at DESC
            LIMIT ${idx}
            """,
            *params,
        )
        return [_fact_to_life_event(dict(r), contact_id) for r in rows]

    # Rebuild conditions with f. prefix for the JOIN query to avoid ambiguous column refs.
    join_conditions = [
        "f.predicate = 'life_event'",
        "f.scope = 'relationship'",
        "f.validity = 'active'",
    ]
    join_params: list[Any] = []
    join_idx = 1
    if type_name is not None:
        join_conditions.append(f"f.metadata->>'life_event_type' = ${join_idx}")
        join_params.append(type_name)
        join_idx += 1
    join_params.append(limit)
    where_join = " AND ".join(join_conditions)

    # Join contacts to resolve contact_id from subject
    rows = await pool.fetch(
        f"""
        SELECT f.id, f.content, f.valid_at, f.created_at, f.metadata,
               c.id AS _contact_id,
               COALESCE(
                   NULLIF(TRIM(CONCAT_WS(' ',
                       COALESCE(c.first_name, ''),
                       COALESCE(c.last_name, '')
                   )), ''),
                   c.nickname,
                   'Unknown'
               ) AS contact_name
        FROM facts f
        JOIN contacts c ON f.subject = 'contact:' || c.id::text
        WHERE {where_join}
        ORDER BY f.valid_at DESC NULLS LAST, f.created_at DESC
        LIMIT ${join_idx}
        """,
        *join_params,
    )
    results = []
    for row in rows:
        cid = row["_contact_id"]
        d = _fact_to_life_event(dict(row), cid)
        d["contact_name"] = row["contact_name"]
        results.append(d)
    return results
