"""Exact owner-admitted candidate decisions; domain commit is the receipt.

Authentication is request admission, not a cross-database COMMIT fence. A later
session revocation may follow an already admitted operation. A disconnected
client reads the durable decision instead of assuming the fact rolled back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from butlers.core import entity_graph_edges
from butlers.core.fact_authority import current_fact_write_context
from butlers.tools.relationship.fact_authority import REPORT_COLUMNS, locked_report
from butlers.tools.relationship.fact_coverage import record_coverage
from butlers.tools.relationship.fact_temporal import PACKET_COLUMNS
from butlers.tools.relationship.identity_slots import identity_slot_key, lock_identity_slot


class IdentityDecisionConflict(ValueError):
    """The exact selected fact is stale or its active identity slot conflicts."""


async def _has_other_live_identity(conn: Any, row: Any) -> bool:
    predicate, value = row["predicate"], row["object"]
    if predicate == "prefers-channel":
        # A preferred channel belongs to one subject, not a shared recipient
        # identity. Another person's email preference is not a collision.
        return False
    if predicate == "has-phone":
        digits = "".join(c for c in value if c.isdigit())
        return bool(
            await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM relationship.entity_facts f JOIN public.entities e "
                "ON e.id=f.subject WHERE f.validity='active' AND f.predicate=$1 AND f.subject<>$2 "
                "AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL "
                "AND (regexp_replace(f.object,'\\D','','g')=$3 OR (length($3)>=8 AND "
                "length(regexp_replace(f.object,'\\D','','g'))>=8 AND "
                "abs(length(regexp_replace(f.object,'\\D','','g'))-length($3))<=2 AND "
                "(right(regexp_replace(f.object,'\\D','','g'),length($3))=$3 OR "
                "$3 LIKE '%' || regexp_replace(f.object,'\\D','','g')))))",
                predicate,
                row["subject"],
                digits,
            )
        )
    normalized = identity_slot_key(predicate, value).split(":", 3)[-1]
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM relationship.entity_facts f JOIN public.entities e "
            "ON e.id=f.subject WHERE f.validity='active' AND f.predicate=$1 AND f.subject<>$2 "
            "AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL "
            "AND CASE WHEN f.predicate='has-handle' AND lower(trim(f.object)) LIKE 'telegram:%' "
            "THEN 'telegram:' || ltrim(substr(lower(trim(f.object)),10),'@') "
            "ELSE lower(trim(f.object)) END=$3)",
            predicate,
            row["subject"],
            normalized,
        )
    )


async def decide_identity_fact(
    conn: Any,
    *,
    entity_id: uuid.UUID,
    fact_id: uuid.UUID,
    decision: Literal["adopt", "reject", "confirm"],
) -> dict[str, Any]:
    """Called inside one owned Relationship transaction; no second acquisition."""
    context = current_fact_write_context()
    if not context.owner_class:
        raise PermissionError("verified owner request admission is required")
    initial = await conn.fetchrow(
        f"SELECT subject,predicate,object,object_kind,{REPORT_COLUMNS} "
        "FROM relationship.entity_facts WHERE id=$1",
        fact_id,
    )
    if initial is None or initial["subject"] != entity_id:
        raise LookupError("selected fact does not exist on this entity")
    context = await locked_report(
        conn,
        context,
        subject=entity_id,
        object=initial["object"],
        object_kind=initial["object_kind"],
        src="dashboard",
        extra_entity_ids={initial["authority_entity_id"]}
        if initial["authority_entity_id"]
        else set(),
    )
    # Entity locks precede identity-slot locks across writer and decision paths,
    # including callers that already own merge/lifecycle entity locks.
    await lock_identity_slot(conn, initial["predicate"], initial["object"], entity_id)
    row = await conn.fetchrow(
        f"SELECT *, {PACKET_COLUMNS} FROM relationship.entity_facts WHERE id=$1 FOR UPDATE", fact_id
    )
    receipt = await conn.fetchrow(
        "SELECT decision, fact_result_id, decided_at FROM relationship.fact_identity_decisions "
        "WHERE fact_id=$1",
        fact_id,
    )
    if receipt:
        if receipt["decision"] != decision:
            raise IdentityDecisionConflict("a different exact decision already committed")
        return {
            "fact_id": receipt["fact_result_id"],
            "decision": decision,
            "decided_at": receipt["decided_at"],
            "replayed": True,
        }
    entity = await conn.fetchrow("SELECT metadata FROM public.entities WHERE id=$1", entity_id)
    if (
        entity is None
        or (entity["metadata"] or {}).get("merged_into")
        or ((entity["metadata"] or {}).get("deleted_at"))
    ):
        raise IdentityDecisionConflict("selected entity is no longer available")
    if row is None or any(
        row[key] != initial[key] for key in ("subject", "predicate", "object", "object_kind")
    ):
        raise IdentityDecisionConflict("selected fact changed")
    expected = "active" if decision == "confirm" else "candidate"
    if row["validity"] != expected:
        raise IdentityDecisionConflict("selected assertion is no longer eligible")
    now = datetime.now(UTC)
    if decision == "reject":
        await conn.execute(
            "UPDATE relationship.entity_facts SET validity='retracted',updated_at=$2 WHERE id=$1",
            fact_id,
            now,
        )
    else:
        if decision == "adopt":
            if row["predicate"] == "prefers-channel":
                from butlers.tools.relationship.relationship_assert_fact import (
                    _entity_has_reachability_fact,
                    _fence_prefers_channel,
                    _supersede_active_prefers_channel,
                )

                await _fence_prefers_channel(conn, entity_id)
                if not await _entity_has_reachability_fact(conn, entity_id, row["object"]):
                    raise IdentityDecisionConflict("preferred channel is not actively reachable")
                await _supersede_active_prefers_channel(conn, entity_id, validity="superseded")
            if await _has_other_live_identity(conn, row):
                raise IdentityDecisionConflict("handle already belongs to another live entity")
            occupied = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM relationship.entity_facts WHERE subject=$1 "
                "AND predicate=$2 AND object=$3 AND validity='active')",
                entity_id,
                row["predicate"],
                row["object"],
            )
            if occupied:
                raise IdentityDecisionConflict("active identity slot already occupied")
        await conn.execute(
            "UPDATE relationship.entity_facts SET validity='active',verified=true, "
            "confirmed_by_entity_id=$2,confirmed_by_original_entity_id=$3,confirmed_at=$4, "
            "confirmation_source=$5,updated_at=$4 WHERE id=$1",
            fact_id,
            context.live_entity_id,
            context.original_entity_id,
            now,
            "dashboard_" + decision,
        )
        if row["object_kind"] == "entity":
            await entity_graph_edges.project_entity_graph_edge(
                conn,
                source_schema="relationship",
                source_table="entity_facts",
                source_id=fact_id,
                subject_entity_id=entity_id,
                predicate=row["predicate"],
                object_entity_id=uuid.UUID(row["object"]),
            )
        await record_coverage(
            conn,
            subject=entity_id,
            predicate=row["predicate"],
            src=row["src"],
            outcome="present",
            observed_at=row["observed_at"] or now,
        )
        from butlers.tools.relationship.relationship_assert_fact import (
            AssertOutcome,
            AssertResult,
            _answer_knowledge_gaps,
        )

        await _answer_knowledge_gaps(
            conn,
            dict(
                subject=entity_id,
                predicate=row["predicate"],
                object=row["object"],
                object_kind=row["object_kind"],
            ),
            AssertResult(AssertOutcome.inserted, fact_id),
        )
    await conn.execute(
        "INSERT INTO relationship.fact_identity_decisions "
        "(fact_id,decision,decided_at,owner_entity_id,owner_original_entity_id,fact_result_id) "
        "VALUES($1,$2,$3,$4,$5,$1)",
        fact_id,
        decision,
        now,
        context.live_entity_id,
        context.original_entity_id,
    )
    return {"fact_id": fact_id, "decision": decision, "decided_at": now, "replayed": False}


def attribution_select_sql(alias: str = "f") -> str:
    """Read-model fields; the historical UUID is intentionally never joined."""
    return f"""
      {alias}.content_authority, {alias}.confirmed_at, {alias}.confirmed_by_entity_id,
      {alias}.confirmation_source,
      jsonb_build_object(
        'entity_id', (SELECT e.id FROM public.entities e WHERE e.id={alias}.authority_entity_id
          AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL),
        'name', (SELECT e.canonical_name FROM public.entities e
          WHERE e.id={alias}.authority_entity_id
          AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL),
        'availability', CASE
          WHEN {alias}.content_authority IS NULL THEN 'legacy_unknown'
          WHEN EXISTS(SELECT 1 FROM public.entities e WHERE e.id={alias}.authority_entity_id
            AND e.metadata->>'merged_into' IS NULL AND e.metadata->>'deleted_at' IS NULL)
            THEN 'available'
          WHEN {alias}.content_authority='system' AND {alias}.authority_original_entity_id IS NULL
            THEN 'system'
          WHEN {alias}.authority_original_entity_id IS NULL THEN 'unresolved'
          ELSE 'unavailable' END) AS reported_by
    """


def attribution_response(row: Any) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "content_authority",
            "reported_by",
            "confirmed_at",
            "confirmed_by_entity_id",
            "confirmation_source",
        )
    }
