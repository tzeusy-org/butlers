"""Gifts — track gift ideas through the pipeline backed by SPO facts.

Each gift is a property fact in the facts table (supersession by subject key):
  subject   = contact:{contact_id}:gift:{description_slug}
  predicate = 'gift'
  content   = description
  metadata  = {occasion, status}
  valid_at  = NULL (property fact — status updates supersede)
  scope     = 'relationship'
  entity_id = contact's entity UUID (resolved via contacts.entity_id)

The response shape is backward compatible with the legacy gifts table.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.relationship.feed import _log_activity

logger = logging.getLogger(__name__)

# Valid gift status pipeline order
_GIFT_STATUS_ORDER = ["idea", "purchased", "wrapped", "given", "thanked"]

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _slug(text: str) -> str:
    """Convert text to a simple slug for use in subject keys."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


def _fact_to_gift(row: dict[str, Any], contact_id: uuid.UUID) -> dict[str, Any]:
    """Convert a facts row to the gifts API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return {
        "id": row["id"],
        "contact_id": contact_id,
        "description": row.get("content", ""),
        "occasion": meta.get("occasion"),
        "status": meta.get("status", "idea"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("created_at"),
    }


async def gift_add(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    description: str,
    occasion: str | None = None,
) -> dict[str, Any]:
    """Add a gift idea for a contact."""
    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)
    embedding_engine = _get_embedding_engine()

    # Subject encodes contact + gift slug for independent per-gift supersession
    subject = f"contact:{contact_id}:gift:{_slug(description)}"

    fact_metadata: dict[str, Any] = {"status": "idea"}
    if occasion is not None:
        fact_metadata["occasion"] = occasion

    fact_id = (
        await store_fact(
            pool,
            subject=subject,
            predicate="gift",
            content=description,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="relationship",
            entity_id=None,  # None so supersession uses subject key (per-gift)
            valid_at=None,  # property fact — supersedes previous for same subject
            metadata=fact_metadata,
        )
    )["id"]

    result: dict[str, Any] = {
        "id": fact_id,
        "contact_id": contact_id,
        "description": description,
        "occasion": occasion,
        "status": "idea",
        "created_at": now,
        "updated_at": now,
    }
    await _log_activity(pool, contact_id, "gift_added", f"Added gift idea: '{description}'")
    return result


async def gift_update_status(pool: asyncpg.Pool, gift_id: uuid.UUID, status: str) -> dict[str, Any]:
    """Update gift status, validating pipeline order."""
    if status not in _GIFT_STATUS_ORDER:
        raise ValueError(f"Invalid status '{status}'. Must be one of {_GIFT_STATUS_ORDER}")

    from butlers.modules.memory.storage import store_fact

    row = await pool.fetchrow(
        "SELECT id, subject, content, metadata, entity_id FROM facts WHERE id = $1",
        gift_id,
    )
    if row is None:
        raise ValueError(f"Gift {gift_id} not found")

    meta = row["metadata"] or {}
    if isinstance(meta, str):
        meta = json.loads(meta)

    current_status = meta.get("status", "idea")
    current_idx = _GIFT_STATUS_ORDER.index(current_status)
    new_idx = _GIFT_STATUS_ORDER.index(status)
    if new_idx <= current_idx:
        raise ValueError(
            f"Cannot move from '{current_status}' to '{status}'. "
            f"Pipeline: {' -> '.join(_GIFT_STATUS_ORDER)}"
        )

    # Extract contact_id from subject (format: contact:{contact_id}:gift:{slug})
    parts = row["subject"].split(":")
    contact_id_str = parts[1] if len(parts) >= 2 else None
    contact_id = uuid.UUID(contact_id_str) if contact_id_str else None

    embedding_engine = _get_embedding_engine()
    description = row["content"]

    new_metadata = dict(meta)
    new_metadata["status"] = status

    new_fact_id = (
        await store_fact(
            pool,
            subject=row["subject"],
            predicate="gift",
            content=description,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="relationship",
            entity_id=None,  # None so supersession uses subject key (per-gift)
            valid_at=None,  # property fact — supersedes previous
            metadata=new_metadata,
        )
    )["id"]

    now = datetime.now(UTC)
    result: dict[str, Any] = {
        "id": new_fact_id,
        "contact_id": contact_id,
        "description": description,
        "occasion": meta.get("occasion"),
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    if contact_id is not None:
        await _log_activity(
            pool,
            contact_id,
            "gift_status_updated",
            f"Gift '{description}' status: {current_status} -> {status}",
        )
    return result


async def gift_list(
    pool: asyncpg.Pool, contact_id: uuid.UUID, status: str | None = None
) -> list[dict[str, Any]]:
    """List gifts for a contact, optionally filtered by status."""
    conditions = [
        "subject LIKE $1",
        "predicate = 'gift'",
        "scope = 'relationship'",
        "validity = 'active'",
        "valid_at IS NULL",
    ]
    params: list[Any] = [f"contact:{contact_id}:gift:%"]
    idx = 2

    if status is not None:
        conditions.append(f"metadata->>'status' = ${idx}")
        params.append(status)
        idx += 1

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT id, content, created_at, metadata
        FROM facts
        WHERE {where}
        ORDER BY created_at DESC
        """,
        *params,
    )
    return [_fact_to_gift(dict(r), contact_id) for r in rows]
