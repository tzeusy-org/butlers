"""Reach-out drafts — an owner-authored message that has NOT been sent.

Each draft is a temporal fact in the facts table (append-only coexistence):
  subject   = entity:{entity_id}
  predicate = 'reach_out_draft'
  content   = draft message text
  metadata  = {channel, status: 'draft'}
  valid_at  = created_at (temporal — every draft coexists independently)
  scope     = 'relationship'
  entity_id = entity UUID (same value as the subject's id)

Drafting is deliberately inert.  Nothing in this module contacts a channel,
queues an outbound message, or calls ``notify()`` — a draft becomes an actual
message only when the owner separately chooses to send it.  Keep it that way:
"draft" must mean drafted.

``reach_out_draft`` is not seeded in ``predicate_registry``; ``store_fact``
auto-registers a novel predicate with ``status='proposed'`` (memory storage
D4), which is the intended lifecycle for a predicate this new.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

#: Only the draft state exists today.  A sent state would need a real send
#: path, which this surface deliberately does not have.
DRAFT_STATUS = "draft"

#: A repeat of the same draft text for the same entity inside this window is
#: treated as a double-submit rather than a second draft.
_DEDUP_WINDOW = timedelta(hours=1)

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


async def reach_out_draft_create(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    message: str,
    *,
    channel: str | None = None,
) -> dict[str, Any]:
    """Store a reach-out draft for *entity_id*.  Sends nothing.

    Args:
        pool: Database connection pool.
        entity_id: The entity the draft is addressed to.
        message: The draft message text.
        channel: Optional channel the owner has in mind (e.g. ``telegram``).
            Recorded as intent only; no delivery is attempted.

    Returns the draft shape, or ``{"skipped": "duplicate", "existing_id": ...}``
    when the identical text was already drafted for this entity within the
    last hour.
    """
    if not message or not message.strip():
        raise ValueError("Reach-out draft message is required")

    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)
    text = message.strip()

    existing = await pool.fetchrow(
        """
        SELECT id FROM facts
        WHERE entity_id = $1
          AND predicate = 'reach_out_draft'
          AND scope = 'relationship'
          AND validity = 'active'
          AND content = $2
          AND created_at >= $3
        LIMIT 1
        """,
        entity_id,
        text,
        now - _DEDUP_WINDOW,
    )
    if existing is not None:
        return {"skipped": "duplicate", "existing_id": str(existing["id"])}

    fact_metadata: dict[str, Any] = {"status": DRAFT_STATUS}
    if channel is not None:
        fact_metadata["channel"] = channel

    fact_id = (
        await store_fact(
            pool,
            subject=f"entity:{entity_id}",
            predicate="reach_out_draft",
            content=text,
            embedding_engine=_get_embedding_engine(),
            permanence="volatile",
            scope="relationship",
            entity_id=entity_id,
            valid_at=now,  # temporal — drafts append, they never supersede
            metadata=fact_metadata,
        )
    )["id"]

    return {
        "id": fact_id,
        "entity_id": entity_id,
        "message": text,
        "channel": channel,
        "status": DRAFT_STATUS,
        "created_at": now,
    }
