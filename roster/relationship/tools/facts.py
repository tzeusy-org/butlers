"""Quick facts — store key-value facts about contacts via the SPO facts table.

Facts are always stored with ``entity_id`` set (resolved from the contact's
linked entity).  The ``subject`` field uses ``contact:{contact_id}`` as a
grouping key.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship.feed import _log_activity

logger = logging.getLogger(__name__)


async def _resolve_entity_id(pool: asyncpg.Pool, contact_id: uuid.UUID) -> uuid.UUID | None:
    """Look up the entity_id linked to a contact. Returns None if unlinked."""
    try:
        return await pool.fetchval("SELECT entity_id FROM contacts WHERE id = $1", contact_id)
    except Exception:
        return None


async def _fact_set_spo(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    key: str,
    value: str,
) -> dict[str, Any]:
    """Write a property fact to the facts table.

    The fact is stored with ``entity_id`` (resolved from the contact's linked
    entity) so it is visible on the entity detail page.  Every contact must
    have a linked entity — raises ``ValueError`` if not.
    """
    subject = f"contact:{contact_id}"
    entity_id = await _resolve_entity_id(pool, contact_id)

    if entity_id is None:
        raise ValueError(
            f"Contact {contact_id} has no linked entity. "
            "Contacts must always resolve to an entity before storing facts."
        )

    # Supersede existing active fact with same predicate on this entity
    await pool.execute(
        """
        UPDATE facts
        SET validity = 'superseded'
        WHERE entity_id = $1
          AND predicate = $2
          AND scope = 'global'
          AND validity = 'active'
          AND valid_at IS NULL
        """,
        entity_id,
        key,
    )

    row = await pool.fetchrow(
        """
        INSERT INTO facts (subject, predicate, content, metadata, validity, scope, entity_id)
        VALUES ($1, $2, $3, $4, 'active', 'global', $5)
        RETURNING id, subject, predicate, content, metadata, created_at
        """,
        subject,
        key,
        value,
        json.dumps({}),
        entity_id,
    )
    if row is None:
        raise RuntimeError("INSERT INTO facts returned no row")
    return {
        "id": row["id"],
        "contact_id": contact_id,
        "key": row["predicate"],
        "value": row["content"],
        "created_at": row["created_at"],
        "updated_at": None,
    }


async def _fact_list_spo(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Read property facts from the facts table.

    Queries by ``entity_id`` (resolved from the contact's linked entity).
    Every contact must have a linked entity — raises ``ValueError`` if not.
    """
    entity_id = await _resolve_entity_id(pool, contact_id)

    if entity_id is None:
        raise ValueError(
            f"Contact {contact_id} has no linked entity. "
            "Contacts must always resolve to an entity before querying facts."
        )

    rows = await pool.fetch(
        """
        SELECT id, predicate, content, created_at
        FROM facts
        WHERE entity_id = $1
          AND scope = 'global'
          AND validity = 'active'
          AND valid_at IS NULL
        ORDER BY predicate
        """,
        entity_id,
    )
    return [
        {
            "id": row["id"],
            "contact_id": contact_id,
            "key": row["predicate"],
            "value": row["content"],
            "created_at": row["created_at"],
            "updated_at": None,
        }
        for row in rows
    ]


async def fact_set(
    pool: asyncpg.Pool, contact_id: uuid.UUID, key: str, value: str
) -> dict[str, Any]:
    """Set a quick fact for a contact (UPSERT via SPO facts table)."""
    result = await _fact_set_spo(pool, contact_id, key, value)
    await _log_activity(pool, contact_id, "fact_set", f"Set fact '{key}' = '{value}'")
    return result


async def fact_list(pool: asyncpg.Pool, contact_id: uuid.UUID) -> list[dict[str, Any]]:
    """List all quick facts for a contact (reads from SPO facts table)."""
    return await _fact_list_spo(pool, contact_id)
