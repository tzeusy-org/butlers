"""Quick facts — store key-value facts about contacts."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship.feed import _log_activity


async def _fact_set_spo(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    key: str,
    value: str,
) -> dict[str, Any] | None:
    """Write a property fact to the facts table. Returns result dict or None on failure."""
    try:
        subject = str(contact_id)
        await pool.execute(
            """
            UPDATE facts
            SET validity = 'superseded'
            WHERE subject = $1
              AND predicate = $2
              AND validity = 'active'
              AND valid_at IS NULL
            """,
            subject,
            key,
        )
        row = await pool.fetchrow(
            """
            INSERT INTO facts (subject, predicate, content, metadata, validity, scope)
            VALUES ($1, $2, $3, $4, 'active', 'global')
            RETURNING id, subject, predicate, content, metadata, created_at
            """,
            subject,
            key,
            value,
            json.dumps({}),
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
    """Read property facts from the facts table. Returns list or None on failure."""
    try:
        subject = str(contact_id)
        rows = await pool.fetch(
            """
            SELECT id, predicate, content, created_at
            FROM facts
            WHERE subject = $1
              AND validity = 'active'
              AND valid_at IS NULL
            ORDER BY predicate
            """,
            subject,
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
