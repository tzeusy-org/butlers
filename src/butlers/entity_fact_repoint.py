"""Lightweight entity-fact repointing primitive shared by merge paths."""

from __future__ import annotations

import uuid
from typing import Any


async def repoint_facts_on_conn(
    conn: Any,
    src_uuid: uuid.UUID,
    tgt_uuid: uuid.UUID,
) -> dict[str, int]:
    """Re-point memory ``facts`` rows on an existing transaction connection.

    Property conflicts are resolved by confidence while temporal facts always
    coexist. The caller owns the surrounding transaction.
    """
    facts_repointed = 0
    facts_superseded = 0
    edge_facts_repointed = 0
    edge_facts_superseded = 0

    src_facts = await conn.fetch(
        "SELECT id, scope, predicate, confidence, valid_at FROM facts "
        "WHERE entity_id = $1 AND validity IN ('active', 'fading')",
        src_uuid,
    )

    for src_fact in src_facts:
        conflict = None
        if src_fact["valid_at"] is None:
            conflict = await conn.fetchrow(
                "SELECT id, confidence FROM facts "
                "WHERE entity_id = $1 AND scope = $2 AND predicate = $3 "
                "AND validity IN ('active', 'fading') AND valid_at IS NULL",
                tgt_uuid,
                src_fact["scope"],
                src_fact["predicate"],
            )

        if conflict is None:
            await conn.execute(
                "UPDATE facts SET entity_id = $1 WHERE id = $2",
                tgt_uuid,
                src_fact["id"],
            )
            facts_repointed += 1
        else:
            src_confidence = src_fact["confidence"]
            tgt_confidence = conflict["confidence"]

            if src_confidence > tgt_confidence:
                await conn.execute(
                    "UPDATE facts SET validity = 'superseded', supersedes_id = $1 WHERE id = $2",
                    src_fact["id"],
                    conflict["id"],
                )
                await conn.execute(
                    "UPDATE facts SET entity_id = $1 WHERE id = $2",
                    tgt_uuid,
                    src_fact["id"],
                )
            else:
                await conn.execute(
                    "UPDATE facts SET validity = 'superseded', supersedes_id = $1 WHERE id = $2",
                    conflict["id"],
                    src_fact["id"],
                )
            facts_superseded += 1

    obj_facts = await conn.fetch(
        "SELECT id, entity_id, scope, predicate, confidence, valid_at FROM facts "
        "WHERE object_entity_id = $1 AND validity IN ('active', 'fading')",
        src_uuid,
    )

    for obj_fact in obj_facts:
        edge_conflict = None
        if obj_fact["valid_at"] is None:
            edge_conflict = await conn.fetchrow(
                "SELECT id, confidence FROM facts "
                "WHERE entity_id = $1 AND object_entity_id = $2 "
                "AND scope = $3 AND predicate = $4 "
                "AND validity IN ('active', 'fading') AND valid_at IS NULL",
                obj_fact["entity_id"],
                tgt_uuid,
                obj_fact["scope"],
                obj_fact["predicate"],
            )

        if edge_conflict is None:
            await conn.execute(
                "UPDATE facts SET object_entity_id = $1 WHERE id = $2",
                tgt_uuid,
                obj_fact["id"],
            )
            edge_facts_repointed += 1
        else:
            src_confidence = obj_fact["confidence"]
            tgt_confidence = edge_conflict["confidence"]

            if src_confidence > tgt_confidence:
                await conn.execute(
                    "UPDATE facts SET validity = 'superseded', supersedes_id = $1 WHERE id = $2",
                    obj_fact["id"],
                    edge_conflict["id"],
                )
                await conn.execute(
                    "UPDATE facts SET object_entity_id = $1 WHERE id = $2",
                    tgt_uuid,
                    obj_fact["id"],
                )
            else:
                await conn.execute(
                    "UPDATE facts SET validity = 'superseded', supersedes_id = $1 WHERE id = $2",
                    edge_conflict["id"],
                    obj_fact["id"],
                )
            edge_facts_superseded += 1

    return {
        "facts_repointed": facts_repointed,
        "facts_superseded": facts_superseded,
        "edge_facts_repointed": edge_facts_repointed,
        "edge_facts_superseded": edge_facts_superseded,
    }


__all__ = ["repoint_facts_on_conn"]
