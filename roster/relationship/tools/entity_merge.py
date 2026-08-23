"""FastAPI-free audited relationship entity merge service.

The dashboard and operator reconciliation paths share this transaction so row
locking, conflict resolution, reference rewiring, tombstoning, and audit history
cannot drift.  The optional locked guard is the reconciliation seam required by
``REQ-entity-identity-002``: it runs after both rows are locked and validated but
before any merge write occurs.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

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


LockedMergeGuard = Callable[[asyncpg.Connection, LockedEntityPair], Awaitable[None]]


async def merge_entity_pair(
    pool: asyncpg.Pool,
    *,
    source_entity_id: UUID,
    target_entity_id: UUID,
    locked_guard: LockedMergeGuard | None = None,
    _audit_entity_order: tuple[UUID, UUID] | None = None,
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

            subject_facts_rewired = await conn.fetchval(
                """
                WITH updated AS (
                    UPDATE relationship.entity_facts
                    SET subject = $2,
                        updated_at = now()
                    WHERE subject = $1
                      AND validity = 'active'
                    RETURNING id
                )
                SELECT count(*) FROM updated
                """,
                source_entity_id,
                target_entity_id,
            )

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

            object_facts_rewired = await conn.fetchval(
                """
                WITH updated AS (
                    UPDATE relationship.entity_facts
                    SET object = $2,
                        updated_at = now()
                    WHERE object_kind = 'entity'
                      AND object = $1
                      AND validity = 'active'
                    RETURNING id
                )
                SELECT count(*) FROM updated
                """,
                source_text,
                target_text,
            )

            from butlers.modules.memory.tools.entities import _repoint_facts_on_conn

            await _repoint_facts_on_conn(conn, source_entity_id, target_entity_id)

            await conn.execute(
                """
                UPDATE contact_entity_map
                SET entity_id = $2
                WHERE entity_id = $1
                """,
                source_entity_id,
                target_entity_id,
            )

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

    return EntityMergeResult(
        kept_entity_id=target_entity_id,
        tombstoned_entity_id=source_entity_id,
        subject_facts_rewired=int(subject_facts_rewired),
        object_facts_rewired=int(object_facts_rewired),
        review_id=review_id,
    )
