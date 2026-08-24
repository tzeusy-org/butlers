"""Notes — create, list, and search notes about contacts backed by SPO facts.

Each note is a temporal fact in the facts table (append-only, no supersession):
  subject   = contact:{contact_id}
  predicate = 'contact_note'
  content   = note text
  metadata  = {emotion}
  valid_at  = created_at  (temporal — each note coexists independently)
  scope     = 'relationship'
  entity_id = contact's entity UUID (resolved via contacts.entity_id)

The response shape is backward compatible with the legacy notes table:
  {id, contact_id, body, content, emotion, created_at}
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
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


def _fact_to_note(row: dict[str, Any], contact_id: uuid.UUID) -> dict[str, Any]:
    """Convert a facts row to the notes API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    note_text = row.get("content", "")
    return {
        "id": row["id"],
        "contact_id": contact_id,
        "body": note_text,
        "content": note_text,
        "emotion": meta.get("emotion"),
        "created_at": row.get("created_at"),
    }


async def note_create(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    content: str | None = None,
    body: str | None = None,
    title: str | None = None,
    emotion: str | None = None,
) -> dict[str, Any]:
    """Create a note about a contact."""
    note_text = body if body is not None else content
    if not note_text:
        raise ValueError("Note content/body is required")

    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)

    # Idempotency guard: same note text for same contact within 1 hour.
    one_hour_ago = now - timedelta(hours=1)
    existing = await pool.fetchrow(
        """
        SELECT id FROM facts
        WHERE subject = $1
          AND predicate = 'contact_note'
          AND scope = 'relationship'
          AND validity = 'active'
          AND content = $2
          AND created_at >= $3
        """,
        f"contact:{contact_id}",
        note_text,
        one_hour_ago,
    )
    if existing is not None:
        return {"skipped": "duplicate", "existing_id": str(existing["id"])}

    entity_id = await resolve_contact_entity_id(pool, contact_id)
    embedding_engine = _get_embedding_engine()

    fact_metadata: dict[str, Any] = {}
    if emotion is not None:
        fact_metadata["emotion"] = emotion

    fact_id = (
        await store_fact(
            pool,
            subject=f"contact:{contact_id}",
            predicate="contact_note",
            content=note_text,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="relationship",
            entity_id=entity_id,
            valid_at=now,  # temporal — append-only, each note coexists independently
            metadata=fact_metadata,
        )
    )["id"]

    result: dict[str, Any] = {
        "id": fact_id,
        "contact_id": contact_id,
        "body": note_text,
        "content": note_text,
        "emotion": emotion,
        "created_at": now,
    }

    return result


async def note_create_for_entity(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    content: str,
    *,
    emotion: str | None = None,
) -> dict[str, Any]:
    """Create a note keyed directly on an entity (no contact record required).

    ``note_create`` above is contact-keyed and writes ``subject='contact:{id}'``.
    The dashboard's entity surfaces only ever hold an entity UUID, so this
    variant writes ``subject='entity:{id}'`` while landing the same
    ``predicate='contact_note'`` fact the entity notes endpoint already reads
    (it filters on ``entity_id``, not ``subject``).

    Returns ``{"skipped": "duplicate", "existing_id": ...}`` when the identical
    text was already recorded for this entity within the last hour.  The dedup
    query keys on ``entity_id`` so it also catches a contact-keyed duplicate.
    """
    note_text = (content or "").strip()
    if not note_text:
        raise ValueError("Note content is required")

    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)

    existing = await pool.fetchrow(
        """
        SELECT id FROM facts
        WHERE entity_id = $1
          AND predicate = 'contact_note'
          AND scope = 'relationship'
          AND validity = 'active'
          AND content = $2
          AND created_at >= $3
        LIMIT 1
        """,
        entity_id,
        note_text,
        now - timedelta(hours=1),
    )
    if existing is not None:
        return {"skipped": "duplicate", "existing_id": str(existing["id"])}

    fact_metadata: dict[str, Any] = {}
    if emotion is not None:
        fact_metadata["emotion"] = emotion

    fact_id = (
        await store_fact(
            pool,
            subject=f"entity:{entity_id}",
            predicate="contact_note",
            content=note_text,
            embedding_engine=_get_embedding_engine(),
            permanence="stable",
            scope="relationship",
            entity_id=entity_id,
            valid_at=now,  # temporal — append-only, each note coexists independently
            metadata=fact_metadata,
        )
    )["id"]

    return {
        "id": fact_id,
        "entity_id": entity_id,
        "content": note_text,
        "emotion": emotion,
        "created_at": now,
    }


async def note_list(
    pool: asyncpg.Pool, contact_id: uuid.UUID, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """List all notes for a contact, newest first.

    Matches both subject forms: the contact-keyed notes ``note_create`` writes
    and the entity-keyed notes ``note_create_for_entity`` writes.  Callers that
    already hold an entity UUID pass it as *contact_id* (the same
    pass-through the rest of this package accepts).
    """
    rows = await pool.fetch(
        """
        SELECT id, content, created_at, metadata
        FROM facts
        WHERE subject = ANY($1::text[])
          AND predicate = 'contact_note'
          AND scope = 'relationship'
          AND validity = 'active'
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """,
        [f"contact:{contact_id}", f"entity:{contact_id}"],
        limit,
        offset,
    )
    return [_fact_to_note(dict(r), contact_id) for r in rows]


async def note_search(
    pool: asyncpg.Pool, query: str, contact_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """Search notes by content (ILIKE), optionally scoped to a contact."""
    if contact_id is not None:
        rows = await pool.fetch(
            """
            SELECT f.id, f.content, f.created_at, f.metadata,
                   COALESCE(e.canonical_name, 'Unknown') AS contact_name
            FROM facts f
            JOIN contact_entity_map cem ON f.subject = 'contact:' || cem.contact_id::text
            JOIN public.entities e ON e.id = cem.entity_id
            WHERE f.subject = $1
              AND f.predicate = 'contact_note'
              AND f.scope = 'relationship'
              AND f.validity = 'active'
              AND f.content ILIKE '%' || $2 || '%'
            ORDER BY f.created_at DESC
            """,
            f"contact:{contact_id}",
            query,
        )
        results = []
        for row in rows:
            d = _fact_to_note(dict(row), contact_id)
            d["contact_name"] = row["contact_name"]
            results.append(d)
        return results

    rows = await pool.fetch(
        """
        SELECT f.id, f.content, f.created_at, f.metadata,
               COALESCE(e.canonical_name, 'Unknown') AS contact_name,
               cem.contact_id AS _contact_id
        FROM facts f
        JOIN contact_entity_map cem ON f.subject = 'contact:' || cem.contact_id::text
        JOIN public.entities e ON e.id = cem.entity_id
        WHERE f.predicate = 'contact_note'
          AND f.scope = 'relationship'
          AND f.validity = 'active'
          AND f.content ILIKE '%' || $1 || '%'
        ORDER BY f.created_at DESC
        """,
        query,
    )
    results = []
    for row in rows:
        cid = row["_contact_id"]
        d = _fact_to_note(dict(row), cid)
        d["contact_name"] = row["contact_name"]
        results.append(d)
    return results
