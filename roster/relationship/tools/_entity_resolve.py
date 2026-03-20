"""Entity resolution helper for relationship butler SPO fact writes.

Resolves a contact_id to its entity_id in shared.entities via the shared.contacts
entity_id FK.  Every contact MUST have a linked entity; a None return indicates
a data integrity issue that should be investigated.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg

logger = logging.getLogger(__name__)


async def resolve_contact_entity_id(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the entity_id for *contact_id*, or None if unavailable.

    Logs a warning if the contact exists but has no entity_id — this
    indicates a data integrity issue since all contacts must link to an entity.
    """
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
    entity_id = row["entity_id"]
    if entity_id is None:
        raise ValueError(
            f"Contact {contact_id} has no linked entity_id. "
            "All contacts must resolve to an entity — this is a data integrity issue."
        )
    return entity_id
