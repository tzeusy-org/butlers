"""Feed — unified temporal activity feed for a contact entity backed by SPO facts.

``feed_get`` aggregates the temporal-fact families recorded for a contact into a
single reverse-chronological stream:

  - ``interaction_%``  (calls, meetings, messages — ``valid_at = occurred_at``)
  - ``life_event``     (promotions, moves, milestones — ``valid_at = happened_at``)
  - ``contact_note``   (free-text notes — ``valid_at = created_at``)
  - ``activity``       (generic activity facts — ``valid_at`` of the activity)

All four families carry a meaningful ``valid_at`` (they are temporal, not
property facts like ``gift``/``loan`` whose ``valid_at`` is NULL), so the feed is
ordered ``valid_at DESC``.  Property/edge facts are intentionally excluded.

The query is keyed on the ``facts.entity_id`` column rather than the ``subject``
string because the family writers use different subject conventions
(``entity:{id}`` for interactions, ``contact:{id}`` for notes/life events) while
all of them set ``entity_id`` to the contact's canonical entity UUID.  Keying on
``entity_id`` therefore unifies the stream regardless of subject convention.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Non-interaction temporal predicates included in the feed.  Interactions are
# matched separately via ``predicate LIKE 'interaction_%'``.
_FEED_PREDICATES: tuple[str, ...] = ("life_event", "contact_note", "activity")


def _feed_kind(predicate: str) -> str:
    """Map a fact predicate to a coarse feed-item kind."""
    if predicate.startswith("interaction_"):
        return "interaction"
    return predicate


def _fact_to_feed_item(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the feed-item API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    predicate = row.get("predicate", "")
    return {
        "id": row["id"],
        "entity_id": row.get("entity_id"),
        "kind": _feed_kind(predicate),
        "predicate": predicate,
        "content": row.get("content") or None,
        "valid_at": row.get("valid_at"),
        "created_at": row.get("created_at"),
        "metadata": meta or None,
    }


async def feed_get(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the unified temporal activity feed for a contact entity.

    Aggregates ``interaction_%``, ``life_event``, ``contact_note``, and
    ``activity`` facts for *entity_id* (scope ``relationship``, validity
    ``active``) ordered by ``valid_at DESC`` (NULLS last, then ``created_at
    DESC`` as a stable tie-breaker).

    Args:
        pool: Database connection pool.
        entity_id: The contact's canonical entity UUID.
        limit: Maximum number of feed items to return.
    """
    rows = await pool.fetch(
        """
        SELECT id, predicate, content, valid_at, created_at, metadata, entity_id
        FROM facts
        WHERE entity_id = $1
          AND scope = 'relationship'
          AND validity = 'active'
          AND (
              predicate LIKE 'interaction_%'
              OR predicate = ANY($2::text[])
          )
        ORDER BY valid_at DESC NULLS LAST, created_at DESC
        LIMIT $3
        """,
        entity_id,
        list(_FEED_PREDICATES),
        limit,
    )
    return [_fact_to_feed_item(dict(r)) for r in rows]
