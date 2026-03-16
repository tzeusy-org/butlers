"""Quick facts — store key-value facts about contacts.

Subject convention:
  - ``contact:{contact_id}`` for contact-scoped facts (no entity linked yet).
  - When the contact has a linked entity, the fact row also gets ``entity_id``
    set so it appears on the entity detail page.
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
        return await pool.fetchval(
            "SELECT entity_id FROM contacts WHERE id = $1", contact_id
        )
    except Exception:
        return None


async def _fact_set_spo(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    key: str,
    value: str,
) -> dict[str, Any] | None:
    """Write a property fact to the facts table. Returns result dict or None on failure.

    If the contact has a linked entity, the fact is stored with ``entity_id``
    so it is visible on the entity detail page.  The subject is always
    ``contact:{contact_id}`` for consistency with other relationship tools.
    """
    try:
        subject = f"contact:{contact_id}"
        entity_id = await _resolve_entity_id(pool, contact_id)

        # Supersede: match by entity_id if available, otherwise by subject.
        # Also handle legacy bare-UUID subjects from before the prefix convention.
        if entity_id is not None:
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
        else:
            await pool.execute(
                """
                UPDATE facts
                SET validity = 'superseded'
                WHERE (subject = $1 OR subject = $3)
                  AND predicate = $2
                  AND validity = 'active'
                  AND valid_at IS NULL
                """,
                subject,
                key,
                str(contact_id),  # legacy bare-UUID compat
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
            return None
        return {
            "id": row["id"],
            "contact_id": contact_id,
            "key": row["predicate"],
            "value": row["content"],
            "created_at": row["created_at"],
            "updated_at": None,
        }
    except asyncpg.UndefinedTableError:
        return None
    except asyncpg.PostgresError:
        return None


async def _fact_list_spo(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
) -> list[dict[str, Any]] | None:
    """Read property facts from the facts table. Returns list or None on failure.

    Queries by ``entity_id`` when the contact has a linked entity (so facts
    promoted during linking are included), falling back to subject match
    for both the ``contact:`` prefix and legacy bare-UUID formats.
    """
    try:
        entity_id = await _resolve_entity_id(pool, contact_id)
        subject = f"contact:{contact_id}"

        if entity_id is not None:
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
        else:
            rows = await pool.fetch(
                """
                SELECT id, predicate, content, created_at
                FROM facts
                WHERE (subject = $1 OR subject = $2)
                  AND validity = 'active'
                  AND valid_at IS NULL
                ORDER BY predicate
                """,
                subject,
                str(contact_id),  # legacy bare-UUID compat
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
    except asyncpg.UndefinedTableError:
        return None
    except asyncpg.PostgresError:
        return None


async def fact_set(
    pool: asyncpg.Pool, contact_id: uuid.UUID, key: str, value: str
) -> dict[str, Any]:
    """Set a quick fact for a contact (UPSERT)."""
    spo = await _fact_set_spo(pool, contact_id, key, value)
    if spo is not None:
        await _log_activity(pool, contact_id, "fact_set", f"Set fact '{key}' = '{value}'")
        return spo

    row = await pool.fetchrow(
        """
        INSERT INTO quick_facts (contact_id, key, value)
        VALUES ($1, $2, $3)
        ON CONFLICT (contact_id, key) DO UPDATE SET value = $3, updated_at = now()
        RETURNING *
        """,
        contact_id,
        key,
        value,
    )
    result = dict(row)
    await _log_activity(pool, contact_id, "fact_set", f"Set fact '{key}' = '{value}'")
    return result


async def fact_list(pool: asyncpg.Pool, contact_id: uuid.UUID) -> list[dict[str, Any]]:
    """List all quick facts for a contact."""
    spo = await _fact_list_spo(pool, contact_id)
    if spo is not None:
        return spo

    rows = await pool.fetch(
        "SELECT * FROM quick_facts WHERE contact_id = $1 ORDER BY key",
        contact_id,
    )
    return [dict(row) for row in rows]
