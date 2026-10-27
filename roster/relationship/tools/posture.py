"""Owner-asserted person posture (``public.entities.posture``).

The owner tells Relationship once that someone has died, is estranged or must not
be contacted; every producer and the ``notify()`` egress then respects it (see
``butlers.core.entity_posture``). Posture is never inferred, and setting it never
deletes anything: ``active`` restores every behavior.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from butlers.core.audit import write_audit_entry
from butlers.core.entity_posture import POSTURES

_BUTLER = "relationship"


async def entity_set_posture(
    pool: asyncpg.Pool,
    entity_id: str | uuid.UUID,
    posture: str,
) -> dict[str, Any]:
    """Set an entity's posture: ``active``, ``memorial``, ``quiet`` or ``no_contact``.

    Call only when the owner states it; never infer posture from a message or a
    silence. A single-column last-write-wins update: setting the posture the entity
    already has is a no-op (``changed=False``) that writes no audit row. The audit
    row names the entity and never the posture value.

    Raises ``ValueError`` for an unknown posture, an unknown entity, or the owner
    entity. The ``public.entities`` trigger (core_256) is what restricts the write
    to the Relationship runtime role.
    """
    if posture not in POSTURES:
        raise ValueError(f"posture must be one of {', '.join(POSTURES)}")
    eid = entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(entity_id)

    row = await pool.fetchrow(
        """
        WITH cur AS (
            SELECT id, posture, 'owner' = ANY(roles) AS is_owner
            FROM public.entities
            WHERE id = $1
        ),
        upd AS (
            UPDATE public.entities e
            SET posture = $2,
                posture_since = CURRENT_DATE,
                posture_set_by = $3,
                updated_at = now()
            FROM cur
            WHERE e.id = cur.id AND NOT cur.is_owner AND cur.posture <> $2
            RETURNING e.id
        )
        SELECT cur.is_owner, EXISTS (SELECT 1 FROM upd) AS changed
        FROM cur
        """,
        eid,
        posture,
        _BUTLER,
    )
    if row is None:
        raise ValueError(f"Entity {eid} not found")
    if row["is_owner"]:
        raise ValueError("The owner entity has no posture")
    if row["changed"]:
        await write_audit_entry(pool, _BUTLER, "entity_set_posture", {"path": str(eid)})
    return {"entity_id": str(eid), "posture": posture, "changed": bool(row["changed"])}
