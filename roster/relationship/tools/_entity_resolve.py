"""Entity resolution helper for relationship butler SPO fact writes.

Resolves a contact_id to its entity_id in shared.entities via the shared.contacts
entity_id FK.  Returns None gracefully if:
- The shared schema / contacts table does not exist.
- The contacts table has no entity_id column.
- The contact has no entity_id set (NULL).
"""

from __future__ import annotations

import uuid

import asyncpg


async def resolve_contact_entity_id(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the entity_id for *contact_id*, or None if unavailable."""
    try:
        row = await pool.fetchrow(
            "SELECT entity_id FROM contacts WHERE id = $1",
            contact_id,
        )
    except asyncpg.UndefinedColumnError:
        return None
    except asyncpg.PostgresError:
        return None
    if row is None:
        return None
    return row["entity_id"]
