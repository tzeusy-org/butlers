"""FastAPI-free audited relationship entity merge service.

The dashboard and operator reconciliation paths share this transaction so row
locking, conflict resolution, reference rewiring, tombstoning, and audit history
cannot drift.  The optional locked guard is the reconciliation seam required by
``REQ-entity-identity-002``: it runs after both rows are locked and validated but
before any merge write occurs.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from butlers.entity_rebind import ENTITY_REBOUND_EVENT_TYPE, rebind_entity_references
from butlers.fleet_events import publish_fleet_event
from butlers.tools.relationship.merge_review import compute_merge_evidence, write_merge_review


class EntityMergeError(RuntimeError):
    """Stable, identifier-blind entity merge failure."""

    def __init__(self, classification: str) -> None:
        self.classification = classification
        super().__init__(classification)


class SameEntityError(EntityMergeError):
    def __init__(self) -> None:
        super().__init__("same_entity")


class SourceEntityNotFoundError(EntityMergeError):
    def __init__(self) -> None:
        super().__init__("source_missing")


class TargetEntityNotFoundError(EntityMergeError):
    def __init__(self) -> None:
        super().__init__("target_missing")


class SourceEntityTombstonedError(EntityMergeError):
    def __init__(self) -> None:
        super().__init__("source_tombstoned")


class TargetEntityTombstonedError(EntityMergeError):
    def __init__(self) -> None:
        super().__init__("target_tombstoned")


class AuditEntityOrderError(EntityMergeError):
    def __init__(self) -> None:
        super().__init__("audit_entity_order_mismatch")


_CLASSIFICATION_RE = re.compile(r"[a-z][a-z0-9_]*\Z")


class LockedGuardRejected(EntityMergeError):
    """A locked-pair precondition rejected the merge without exposing evidence."""

    def __init__(self, category: str) -> None:
        safe_category = (
            category if _CLASSIFICATION_RE.fullmatch(category) else "locked_guard_rejected"
        )
        self.category = safe_category
        super().__init__(safe_category)


@dataclass(frozen=True)
class LockedEntityPair:
    source: Mapping[str, Any]
    target: Mapping[str, Any]


@dataclass(frozen=True)
class EntityMergeResult:
    kept_entity_id: UUID
    tombstoned_entity_id: UUID
    subject_facts_rewired: int
    object_facts_rewired: int
    review_id: UUID
    aliases_added: int = 0
    rebind_id: UUID | None = None
    receipts: tuple[Mapping[str, Any], ...] = ()
    failed_schemas: tuple[str, ...] = ()


LockedMergeGuard = Callable[[asyncpg.Connection, LockedEntityPair], Awaitable[None]]

MEMORY_BEARING_SCHEMAS = (
    "chronicler",
    "chronicler_mem",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "relationship",
    "switchboard",
    "travel",
)


def _rowcount(command_tag: Any) -> int:
    try:
        return int(str(command_tag).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


async def merge_entity_pair(
    pool: asyncpg.Pool,
    *,
    source_entity_id: UUID,
    target_entity_id: UUID,
    locked_guard: LockedMergeGuard | None = None,
    _audit_entity_order: tuple[UUID, UUID] | None = None,
    target_schemas: Sequence[str] = MEMORY_BEARING_SCHEMAS,
) -> EntityMergeResult:
    """Atomically merge one locked entity pair and return content-blind counts."""
    if source_entity_id == target_entity_id:
        raise SameEntityError

    audit_entity_order = (
        (target_entity_id, source_entity_id) if _audit_entity_order is None else _audit_entity_order
    )
    if len(audit_entity_order) != 2 or set(audit_entity_order) != {
        source_entity_id,
        target_entity_id,
    }:
        raise AuditEntityOrderError

    rebind_id = uuid4()
    receipt_rows: list[Mapping[str, Any]] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            lock_rows = await conn.fetch(
                """
                SELECT
                    id,
                    canonical_name,
                    entity_type,
                    aliases,
                    metadata,
                    roles,
                    updated_at
                FROM public.entities
                WHERE id = ANY($1::uuid[])
                ORDER BY id
                FOR UPDATE
                """,
                [source_entity_id, target_entity_id],
            )
            lock_map = {row["id"]: row for row in lock_rows}

            source = lock_map.get(source_entity_id)
            if source is None:
                raise SourceEntityNotFoundError
            source_metadata: dict[str, Any] = source["metadata"] or {}
            if "merged_into" in source_metadata:
                raise SourceEntityTombstonedError

            target = lock_map.get(target_entity_id)
            if target is None:
                raise TargetEntityNotFoundError
            target_metadata: dict[str, Any] = target["metadata"] or {}
            if "merged_into" in target_metadata:
                raise TargetEntityTombstonedError

            locked_pair = LockedEntityPair(source=source, target=target)
            if locked_guard is not None:
                await locked_guard(conn, locked_pair)

            merge_evidence = await compute_merge_evidence(
                conn,
                audit_entity_order[0],
                audit_entity_order[1],
            )

            source_aliases = list(source["aliases"] or [])
            source_name = str(source["canonical_name"] or "").strip()
            target_name = str(target["canonical_name"] or "").strip()
            if source_name and source_name.casefold() != target_name.casefold():
                source_aliases.append(source_name)
            merged_aliases = list(target["aliases"] or [])
            alias_keys = {str(alias).casefold() for alias in merged_aliases}
            aliases_added = 0
            for alias in source_aliases:
                if str(alias).casefold() not in alias_keys:
                    merged_aliases.append(alias)
                    alias_keys.add(str(alias).casefold())
                    aliases_added += 1
            merged_roles = list(dict.fromkeys([*(target["roles"] or []), *(source["roles"] or [])]))
            source_metadata_clean = {
                key: value
                for key, value in source_metadata.items()
                if key not in {"deleted_at", "merged_into", "unidentified"}
            }
            await conn.execute(
                """
                UPDATE public.entities
                SET aliases = $1, roles = $2, metadata = $3, updated_at = now()
                WHERE id = $4
                """,
                merged_aliases,
                merged_roles,
                {**source_metadata_clean, **target_metadata},
                target_entity_id,
            )

            # Exact subject-side collisions preserve the target row and supersede
            # the source row before the remaining active rows are moved.
            await conn.execute(
                """
                UPDATE relationship.entity_facts AS src
                SET validity = 'superseded',
                    updated_at = now()
                WHERE src.subject = $1
                  AND src.validity = 'active'
                  AND EXISTS (
                      SELECT 1 FROM relationship.entity_facts tgt
                      WHERE tgt.subject = $2
                        AND tgt.predicate = src.predicate
                        AND tgt.object = src.object
                        AND tgt.validity = 'active'
                  )
                """,
                source_entity_id,
                target_entity_id,
            )

            # Resolve all active rows for registry-declared single-cardinality
            # predicates. Higher confidence wins; ties keep the target row.
            await conn.execute(
                """
                WITH ranked AS (
                    SELECT
                        ef.id,
                        row_number() OVER (
                            PARTITION BY ef.predicate
                            ORDER BY
                                ef.conf DESC,
                                (ef.subject = $2) DESC,
                                ef.id
                        ) AS rn
                    FROM relationship.entity_facts ef
                    JOIN relationship.entity_predicate_registry pr
                      ON pr.predicate = ef.predicate
                    WHERE ef.subject IN ($1, $2)
                      AND ef.validity = 'active'
                      AND pr.cardinality = 'single'
                )
                UPDATE relationship.entity_facts AS ef
                SET validity = 'superseded',
                    updated_at = now()
                FROM ranked
                WHERE ef.id = ranked.id
                  AND ranked.rn > 1
                """,
                source_entity_id,
                target_entity_id,
            )

            subject_rewired_ids = await conn.fetch(
                """
                UPDATE relationship.entity_facts
                SET subject = $2,
                    updated_at = now()
                WHERE subject = $1
                  AND validity = 'active'
                RETURNING id
                """,
                source_entity_id,
                target_entity_id,
            )
            subject_facts_rewired = len(subject_rewired_ids)

            source_text = str(source_entity_id)
            target_text = str(target_entity_id)
            await conn.execute(
                """
                UPDATE relationship.entity_facts AS src
                SET validity = 'superseded',
                    updated_at = now()
                WHERE src.object_kind = 'entity'
                  AND src.object = $1
                  AND src.validity = 'active'
                  AND EXISTS (
                      SELECT 1 FROM relationship.entity_facts tgt
                      WHERE tgt.subject = src.subject
                        AND tgt.predicate = src.predicate
                        AND tgt.object = $2
                        AND tgt.object_kind = 'entity'
                        AND tgt.validity = 'active'
                  )
                """,
                source_text,
                target_text,
            )

            object_rewired_ids = await conn.fetch(
                """
                UPDATE relationship.entity_facts
                SET object = $2,
                    updated_at = now()
                WHERE object_kind = 'entity'
                  AND object = $1
                  AND validity = 'active'
                RETURNING id
                """,
                source_text,
                target_text,
            )
            object_facts_rewired = len(object_rewired_ids)

            # RFC 0031 write-behind contract: the two rewires above just moved
            # entity_facts.subject/object off the source entity, but the
            # already-projected public.entity_graph_edges rows for those exact
            # facts (keyed on source_id=entity_facts.id) still point at the old
            # id until the next backfill sweep. Scope the edge update to the
            # RETURNED ids from the rewires above -- never a blanket
            # subject_entity_id/object_entity_id = source_entity_id filter --
            # so a stale edge belonging to a fact that was SUPERSEDED (not
            # rewired) by the dedup UPDATEs above is left untouched.
            if subject_rewired_ids:
                await conn.execute(
                    """
                    UPDATE public.entity_graph_edges
                    SET subject_entity_id = $2,
                        updated_at = now()
                    WHERE source_schema = 'relationship'
                      AND source_table = 'entity_facts'
                      AND source_id = ANY($1::uuid[])
                    """,
                    [row["id"] for row in subject_rewired_ids],
                    target_entity_id,
                )
            if object_rewired_ids:
                await conn.execute(
                    """
                    UPDATE public.entity_graph_edges
                    SET object_entity_id = $2,
                        updated_at = now()
                    WHERE source_schema = 'relationship'
                      AND source_table = 'entity_facts'
                      AND source_id = ANY($1::uuid[])
                    """,
                    [row["id"] for row in object_rewired_ids],
                    target_entity_id,
                )

            contact_tag = await conn.execute(
                """
                UPDATE contact_entity_map
                SET entity_id = $2
                WHERE entity_id = $1
                """,
                source_entity_id,
                target_entity_id,
            )

            # memory_catalog is deployed by the core chain but some bounded
            # relationship-only installations and migration harnesses omit it.
            # A savepoint keeps absence from aborting the authority transaction;
            # the subscriber-local handlers still own schema-attributed catalog
            # rows when the projection is installed.
            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        UPDATE public.memory_catalog
                        SET entity_id = CASE WHEN entity_id = $1 THEN $2 ELSE entity_id END,
                            object_entity_id = CASE
                                WHEN object_entity_id = $1 THEN $2 ELSE object_entity_id END
                        WHERE entity_id = $1 OR object_entity_id = $1
                        """,
                        source_entity_id,
                        target_entity_id,
                    )
            except asyncpg.UndefinedTableError:
                pass

            tombstone_metadata = {
                **source_metadata,
                "merged_into": str(target_entity_id),
            }
            await conn.execute(
                """
                UPDATE public.entities
                SET metadata = $1,
                    updated_at = now()
                WHERE id = $2
                """,
                tombstone_metadata,
                source_entity_id,
            )

            review_id = await write_merge_review(
                conn,
                entity_a=audit_entity_order[0],
                entity_b=audit_entity_order[1],
                shared_facts=merge_evidence["shared"],
                divergent_facts=merge_evidence["divergent"],
                outcome="merged",
            )

            relationship_rebound = (
                subject_facts_rewired + object_facts_rewired + _rowcount(contact_tag)
            )
            for schema in dict.fromkeys(target_schemas):
                await conn.execute(
                    """
                    INSERT INTO public.entity_rebind_log (
                        rebind_id, source_entity_id, target_entity_id, target_schema,
                        references_rebound, status, completed_at
                    ) VALUES ($1, $2, $3, $4, $5, 'pending', NULL)
                    """,
                    rebind_id,
                    source_entity_id,
                    target_entity_id,
                    schema,
                    relationship_rebound if schema == "relationship" else 0,
                )
                receipt_rows.append(
                    {
                        "rebind_id": rebind_id,
                        "target_schema": schema,
                        "references_rebound": (
                            relationship_rebound if schema == "relationship" else 0
                        ),
                        "status": "pending",
                        "error_class": None,
                        "completed_at": None,
                    }
                )

    # Relationship owns both the merge authority and memory-module tables in
    # its own schema. Settle that receipt through the same local handler as
    # every other daemon before claiming it is active.
    if "relationship" in dict.fromkeys(target_schemas):
        relationship_receipt = await rebind_entity_references(
            pool,
            rebind_id=rebind_id,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            target_schema="relationship",
        )
        for index, row in enumerate(receipt_rows):
            if row["target_schema"] == "relationship":
                receipt_rows[index] = {
                    "rebind_id": relationship_receipt.rebind_id,
                    "target_schema": relationship_receipt.target_schema,
                    "references_rebound": relationship_receipt.references_rebound,
                    "status": relationship_receipt.status,
                    "error_class": relationship_receipt.error_class,
                    "completed_at": datetime.now(UTC),
                }
                break

    # The ledger is the recovery authority. Live daemons react to this event;
    # startup replay remains the durable recovery path for missed delivery.
    await publish_fleet_event(
        pool,
        ENTITY_REBOUND_EVENT_TYPE,
        {
            "rebind_id": str(rebind_id),
            "source_entity_id": str(source_entity_id),
            "target_entity_id": str(target_entity_id),
        },
    )

    return EntityMergeResult(
        kept_entity_id=target_entity_id,
        tombstoned_entity_id=source_entity_id,
        subject_facts_rewired=int(subject_facts_rewired),
        object_facts_rewired=int(object_facts_rewired),
        review_id=review_id,
        aliases_added=aliases_added,
        rebind_id=rebind_id,
        receipts=tuple(receipt_rows),
        failed_schemas=tuple(
            row["target_schema"] for row in receipt_rows if row["status"] == "failed"
        ),
    )
