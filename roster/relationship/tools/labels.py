"""Labels — create labels and assign them to contacts."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship.contacts import _parse_contact


async def label_create(pool: asyncpg.Pool, name: str, color: str | None = None) -> dict[str, Any]:
    """Create a label."""
    row = await pool.fetchrow(
        "INSERT INTO labels (name, color) VALUES ($1, $2) RETURNING *",
        name,
        color,
    )
    return dict(row)


async def label_assign(
    pool: asyncpg.Pool, label_id: uuid.UUID, contact_id: uuid.UUID
) -> dict[str, Any]:
    """Assign a label to a contact."""
    await pool.execute(
        "INSERT INTO contact_labels (label_id, contact_id) VALUES ($1, $2)",
        label_id,
        contact_id,
    )
    return {"label_id": label_id, "contact_id": contact_id}


async def contact_search_by_label(pool: asyncpg.Pool, label_name: str) -> list[dict[str, Any]]:
    """Search contacts by label name, using entity canonical_name for display (bead 7)."""
    rows = await pool.fetch(
        """
        SELECT c.*,
               COALESCE(
                   e.canonical_name,
                   NULLIF(TRIM(COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '')), ''),
                   c.nickname,
                   'Unknown'
               ) AS canonical_name
        FROM contacts c
        JOIN contact_labels cl ON c.id = cl.contact_id
        JOIN labels l ON cl.label_id = l.id
        LEFT JOIN public.entities e ON e.id = c.entity_id
        WHERE l.name = $1 AND c.listed = true
        ORDER BY COALESCE(
            e.canonical_name,
            NULLIF(TRIM(COALESCE(c.first_name, '') || ' ' || COALESCE(c.last_name, '')), ''),
            c.nickname,
            'Unknown'
        )
        """,
        label_name,
    )
    return [_parse_contact(row) for row in rows]
