"""Content-blind, explicitly authorized WhatsApp shell reconciliation.

This module is deterministic relationship-domain infrastructure for
``REQ-entity-identity-002``.  It is intentionally not imported by daemon,
scheduler, connector, or migration paths; the repository-owned operator command
is its only automatic caller.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import partial
from typing import Any, Literal
from uuid import UUID

import asyncpg

from butlers.tools.relationship.entity_merge import (
    LockedEntityPair,
    LockedGuardRejected,
    merge_entity_pair,
)

_WHATSAPP_IDENTIFIER_RE = re.compile(r"^(?P<value>\d+)(?::\d+)?@(?P<server>s\.whatsapp\.net|lid)$")
_PHONE_SUFFIX_MIN_DIGITS = 8
_PHONE_SUFFIX_MAX_DELTA = 2
_NO_REVIEW_DECISION = "none"
_MERGE_TOOL_NAMES = ("memory_entity_merge", "entity_merge")
_DECISION_STATUSES = ("pending", "approved", "rejected", "abandoned")
_CHANNEL_METADATA_KEYS = frozenset({"unidentified", "source_channel", "source_value"})
_FACT_STORAGE_METADATA_KEYS = frozenset({"unidentified", "source", "source_butler", "source_scope"})
_SEMANTIC_FK_TABLES = frozenset(
    {
        ("relationship", "entity_facts"),
        ("relationship", "merge_reviews"),
    }
)
_CONTROL_RELATIONS = frozenset(
    {
        ("public", "whatsmeow_lid_map"),
        ("relationship", "contact_entity_map"),
        ("relationship", "entity_facts"),
        ("relationship", "facts"),
        ("relationship", "merge_reviews"),
        ("relationship", "pending_actions"),
    }
)
_PROTECTED_ROLES = frozenset({"owner", "system"})
_PROTECTED_METADATA_KEYS = frozenset({"is_system", "system", "system_account", "protected_account"})


class ReconciliationCategory(StrEnum):
    UNIQUE_EMPTY_SHELL = "unique_empty_shell"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    INVALID_IDENTIFIER = "invalid_identifier"
    OWNER_OR_SYSTEM_TARGET = "owner_or_system_target"
    EXISTING_REVIEW_DECISION = "existing_review_decision"
    REFERENCED_SOURCE = "referenced_source"
    PLAN_DRIFT = "plan_drift"


@dataclass(frozen=True)
class PlannedWhatsAppMerge:
    source_entity_id: UUID
    target_entity_id: UUID
    source_updated_at: datetime
    target_updated_at: datetime
    review_state: str


@dataclass(frozen=True)
class WhatsAppReconciliationPlan:
    pairs: Sequence[PlannedWhatsAppMerge]
    counts: Mapping[ReconciliationCategory, int]
    digest: str


@dataclass(frozen=True)
class ContentBlindReconciliationReport:
    mode: Literal["dry_run", "apply"]
    counts: Mapping[str, int]
    planned: int
    applied: int
    plan_digest: str


class WhatsAppReconciliationError(RuntimeError):
    """Stable, content-blind reconciliation failure."""

    def __init__(self, classification: str) -> None:
        self.classification = classification
        super().__init__(classification)


class PlanDigestMismatch(WhatsAppReconciliationError):
    def __init__(self) -> None:
        super().__init__("plan_digest_mismatch")


class ReconciliationPostconditionError(WhatsAppReconciliationError):
    def __init__(self) -> None:
        super().__init__("postcondition_failed")


@dataclass(frozen=True)
class _ParsedWhatsAppIdentifier:
    value: str
    server: Literal["s.whatsapp.net", "lid"]


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    return dict(value)


def _parse_identifier(value: Any) -> _ParsedWhatsAppIdentifier | None:
    if not isinstance(value, str):
        return None
    match = _WHATSAPP_IDENTIFIER_RE.fullmatch(value)
    if match is None:
        return None
    return _ParsedWhatsAppIdentifier(
        value=match.group("value"),
        server=match.group("server"),  # type: ignore[arg-type]
    )


def _approved_provenance(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("unidentified") is not True:
        return False
    if metadata.get("source_channel") == "whatsapp_user_client":
        return True
    return (
        metadata.get("source") == "fact_storage"
        and metadata.get("source_butler") == "general"
        and metadata.get("source_scope") in {"general", "global"}
    )


def _metadata_is_shell_safe(metadata: Mapping[str, Any]) -> bool:
    if not _approved_provenance(metadata):
        return False
    allowed: set[str] = set()
    if metadata.get("source_channel") == "whatsapp_user_client":
        allowed.update(_CHANNEL_METADATA_KEYS)
    if (
        metadata.get("source") == "fact_storage"
        and metadata.get("source_butler") == "general"
        and metadata.get("source_scope") in {"general", "global"}
    ):
        allowed.update(_FACT_STORAGE_METADATA_KEYS)
    return set(metadata) <= allowed


def _owner_or_system(row: Mapping[str, Any]) -> bool:
    roles = {str(role).strip().lower() for role in (row["roles"] or [])}
    if roles & _PROTECTED_ROLES:
        return True
    metadata = _metadata(row["metadata"])
    return any(metadata.get(key) is True for key in _PROTECTED_METADATA_KEYS)


async def _phone_digits_for_identifier(
    executor: Any,
    parsed: _ParsedWhatsAppIdentifier,
) -> str | None:
    if parsed.server == "s.whatsapp.net":
        return parsed.value
    mapped = await executor.fetchval(
        "SELECT pn FROM public.whatsmeow_lid_map WHERE lid = $1",
        parsed.value,
    )
    if not isinstance(mapped, str):
        return None
    return mapped if mapped.isdigit() else None


async def _phone_candidates(
    executor: Any,
    *,
    source_entity_id: UUID,
    digits: str,
) -> list[Any]:
    """Enumerate every distinct live confirmed phone candidate."""
    return await executor.fetch(
        """
        WITH stored AS (
            SELECT
                ef.subject AS entity_id,
                ef.object,
                regexp_replace(ef.object, '\\D', '', 'g') AS digits
            FROM relationship.entity_facts ef
            WHERE ef.predicate = 'has-phone'
              AND ef.object_kind = 'literal'
              AND ef.validity = 'active'
        )
        SELECT DISTINCT
            e.id,
            e.canonical_name,
            e.entity_type,
            e.aliases,
            e.metadata,
            e.roles,
            e.updated_at
        FROM stored
        JOIN public.entities e ON e.id = stored.entity_id
        WHERE e.id <> $1
          AND e.entity_type = 'person'
          AND e.metadata ->> 'merged_into' IS NULL
          AND e.metadata ->> 'deleted_at' IS NULL
          AND COALESCE((e.metadata ->> 'unidentified')::boolean, false) IS NOT TRUE
          AND (
                stored.object = $2
                OR (
                    length($2) >= $3
                    AND length(stored.digits) >= $3
                    AND abs(length(stored.digits) - length($2)) <= $4
                    AND (
                        stored.digits LIKE '%' || $2
                        OR $2 LIKE '%' || stored.digits
                    )
                )
          )
        ORDER BY e.id
        """,
        source_entity_id,
        digits,
        _PHONE_SUFFIX_MIN_DIGITS,
        _PHONE_SUFFIX_MAX_DELTA,
    )


async def _review_state(executor: Any, source_id: UUID, target_id: UUID) -> str:
    merge_outcomes = await executor.fetch(
        """
        SELECT DISTINCT outcome
        FROM relationship.merge_reviews
        WHERE (entity_a = $1 AND entity_b = $2)
           OR (entity_a = $2 AND entity_b = $1)
        ORDER BY outcome
        """,
        source_id,
        target_id,
    )
    pending_statuses = await executor.fetch(
        """
        SELECT DISTINCT status
        FROM relationship.pending_actions
        WHERE tool_name = ANY($3::text[])
          AND status = ANY($4::text[])
          AND tool_args ->> 'source_entity_id' = $1
          AND tool_args ->> 'target_entity_id' = $2
        ORDER BY status
        """,
        str(source_id),
        str(target_id),
        list(_MERGE_TOOL_NAMES),
        list(_DECISION_STATUSES),
    )
    states = [f"review:{row['outcome']}" for row in merge_outcomes]
    states.extend(f"action:{row['status']}" for row in pending_statuses)
    return ",".join(states) if states else _NO_REVIEW_DECISION


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


async def _relation_exists(executor: Any, qualified_name: str) -> bool:
    return bool(await executor.fetchval("SELECT to_regclass($1) IS NOT NULL", qualified_name))


async def _explicit_protected_columns(executor: Any) -> list[Any]:
    """Return current protected UUID columns that are not guaranteed to have FKs."""
    return await executor.fetch(
        """
        SELECT DISTINCT
            ns.nspname AS schema_name,
            relation.relname AS table_name,
            attribute.attname AS column_name
        FROM pg_class relation
        JOIN pg_namespace ns ON ns.oid = relation.relnamespace
        JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
        WHERE relation.relkind IN ('r', 'p')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND (
                (
                    ns.nspname = 'chronicler'
                    AND relation.relname IN ('episodes', 'episode_entities', 'point_events')
                    AND attribute.attname = 'entity_id'
                )
                OR (
                    ns.nspname = 'public'
                    AND relation.relname IN (
                        'entity_info',
                        'memory_catalog',
                        'priority_contacts'
                    )
                    AND attribute.attname = 'entity_id'
                )
                OR (
                    ns.nspname = 'relationship'
                    AND relation.relname IN ('contact_entity_map', 'entity_view_marks')
                    AND attribute.attname = 'entity_id'
                )
                OR (
                    relation.relname = 'calendar_event_entities'
                    AND attribute.attname = 'entity_id'
                )
                OR (
                    relation.relname = 'contacts_source_links'
                    AND attribute.attname = 'local_entity_id'
                )
          )
        ORDER BY ns.nspname, relation.relname, attribute.attname
        """
    )


async def _memory_fact_relations(executor: Any) -> list[Any]:
    """Return every schema-local memory facts relation with entity anchors."""
    return await executor.fetch(
        """
        SELECT ns.nspname AS schema_name, relation.relname AS table_name
        FROM pg_class relation
        JOIN pg_namespace ns ON ns.oid = relation.relnamespace
        WHERE relation.relkind IN ('r', 'p')
          AND relation.relname = 'facts'
          AND EXISTS (
              SELECT 1 FROM pg_attribute attribute
              WHERE attribute.attrelid = relation.oid
                AND attribute.attname = 'entity_id'
                AND attribute.attnum > 0
                AND NOT attribute.attisdropped
          )
          AND EXISTS (
              SELECT 1 FROM pg_attribute attribute
              WHERE attribute.attrelid = relation.oid
                AND attribute.attname = 'object_entity_id'
                AND attribute.attnum > 0
                AND NOT attribute.attisdropped
          )
        ORDER BY ns.nspname, relation.relname
        """
    )


async def _merge_review_reference_exists(
    executor: Any,
    source_id: UUID,
    *,
    allowed_target_id: UUID | None,
) -> bool:
    return bool(
        await executor.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM relationship.merge_reviews
                WHERE (entity_a = $1 OR entity_b = $1)
                  AND (
                        $2::uuid IS NULL
                        OR NOT (
                            (entity_a = $1 AND entity_b = $2)
                            OR (entity_a = $2 AND entity_b = $1)
                        )
                  )
            )
            """,
            source_id,
            allowed_target_id,
        )
    )


async def _pending_merge_reference_exists(executor: Any, source_id: UUID) -> bool:
    return bool(
        await executor.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM relationship.pending_actions
                WHERE tool_name = ANY($2::text[])
                  AND status = ANY($3::text[])
                  AND (
                        tool_args ->> 'source_entity_id' = $1
                        OR tool_args ->> 'target_entity_id' = $1
                  )
            )
            """,
            str(source_id),
            list(_MERGE_TOOL_NAMES),
            list(_DECISION_STATUSES),
        )
    )


async def _known_reference_exists(
    executor: Any,
    source_id: UUID,
    *,
    allowed_review_target_id: UUID | None,
) -> bool:
    if await executor.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM relationship.entity_facts
            WHERE validity = 'active'
              AND (
                    subject = $1
                    OR (object_kind = 'entity' AND object = $2)
              )
        )
        """,
        source_id,
        str(source_id),
    ):
        return True

    for relation in await _memory_fact_relations(executor):
        qualified = (
            f"{_quote_identifier(relation['schema_name'])}."
            f"{_quote_identifier(relation['table_name'])}"
        )
        if await executor.fetchval(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {qualified}
                WHERE entity_id = $1 OR object_entity_id = $1
            )
            """,  # noqa: S608
            source_id,
        ):
            return True

    for reference in await _explicit_protected_columns(executor):
        qualified = (
            f"{_quote_identifier(reference['schema_name'])}."
            f"{_quote_identifier(reference['table_name'])}"
        )
        column = _quote_identifier(reference["column_name"])
        if await executor.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {qualified} WHERE {column} = $1)",  # noqa: S608
            source_id,
        ):
            return True

    if await _merge_review_reference_exists(
        executor,
        source_id,
        allowed_target_id=allowed_review_target_id,
    ):
        return True
    return await _pending_merge_reference_exists(executor, source_id)


async def _catalog_reference_columns(executor: Any) -> list[Any]:
    return await executor.fetch(
        """
        SELECT DISTINCT
            child_ns.nspname AS schema_name,
            child.relname AS table_name,
            child_att.attname AS column_name
        FROM pg_constraint constraint_row
        JOIN pg_class child ON child.oid = constraint_row.conrelid
        JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
        CROSS JOIN LATERAL unnest(constraint_row.conkey)
            WITH ORDINALITY AS child_key(attnum, ordinal_position)
        CROSS JOIN LATERAL unnest(constraint_row.confkey)
            WITH ORDINALITY AS parent_key(attnum, ordinal_position)
        JOIN pg_attribute child_att
          ON child_att.attrelid = child.oid
         AND child_att.attnum = child_key.attnum
        JOIN pg_attribute parent_att
          ON parent_att.attrelid = constraint_row.confrelid
         AND parent_att.attnum = parent_key.attnum
         AND parent_key.ordinal_position = child_key.ordinal_position
        WHERE constraint_row.contype = 'f'
          AND constraint_row.confrelid = 'public.entities'::regclass
          AND parent_att.attname = 'id'
        ORDER BY child_ns.nspname, child.relname, child_att.attname
        """
    )


async def _catalog_reference_exists(executor: Any, source_id: UUID) -> bool:
    references = await _catalog_reference_columns(executor)
    for reference in references:
        table_key = (reference["schema_name"], reference["table_name"])
        if table_key in _SEMANTIC_FK_TABLES:
            continue
        relation = (
            f"{_quote_identifier(reference['schema_name'])}."
            f"{_quote_identifier(reference['table_name'])}"
        )
        column = _quote_identifier(reference["column_name"])
        if await executor.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {relation} WHERE {column} = $1)",  # noqa: S608
            source_id,
        ):
            return True
    return False


async def _source_has_references(
    executor: Any,
    source_id: UUID,
    *,
    allowed_review_target_id: UUID | None = None,
) -> bool:
    if await _known_reference_exists(
        executor,
        source_id,
        allowed_review_target_id=allowed_review_target_id,
    ):
        return True
    return await _catalog_reference_exists(executor, source_id)


async def _lock_reconciliation_relations(conn: asyncpg.Connection) -> None:
    """Fence every relation whose writes could invalidate the locked guard.

    ``NOWAIT`` is deliberate: a writer can acquire a table RowExclusive lock
    before blocking on the already-locked source entity FK.  Waiting here would
    invert the lock order and deadlock.  Failing the reconciliation transaction
    is the safe outcome; a writer that starts after this fence waits until the
    merge commits or rolls back.
    """
    relations = set(_CONTROL_RELATIONS)
    relations.update(
        (row["schema_name"], row["table_name"]) for row in await _catalog_reference_columns(conn)
    )
    relations.update(
        (row["schema_name"], row["table_name"]) for row in await _explicit_protected_columns(conn)
    )
    relations.update(
        (row["schema_name"], row["table_name"]) for row in await _memory_fact_relations(conn)
    )

    existing: list[tuple[str, str]] = []
    for schema_name, table_name in sorted(relations):
        qualified = f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"
        if await _relation_exists(conn, qualified):
            existing.append((schema_name, table_name))
    if not existing:
        return

    targets = ", ".join(
        f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"
        for schema_name, table_name in existing
    )
    try:
        await conn.execute(
            f"LOCK TABLE {targets} IN SHARE ROW EXCLUSIVE MODE NOWAIT"  # noqa: S608
        )
    except asyncpg.LockNotAvailableError:
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value) from None


def _source_shell_shape_is_safe(source: Mapping[str, Any]) -> bool:
    return (
        source["entity_type"] == "person"
        and not (source["aliases"] or [])
        and not (source["roles"] or [])
        and _metadata_is_shell_safe(_metadata(source["metadata"]))
    )


def _empty_counts() -> dict[ReconciliationCategory, int]:
    return {category: 0 for category in ReconciliationCategory}


def _plan_digest(pairs: Sequence[PlannedWhatsAppMerge]) -> str:
    payload = [
        {
            "review_state": pair.review_state,
            "source_entity_id": str(pair.source_entity_id),
            "source_updated_at": pair.source_updated_at.isoformat(),
            "target_entity_id": str(pair.target_entity_id),
            "target_updated_at": pair.target_updated_at.isoformat(),
        }
        for pair in pairs
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _build_plan_on_executor(executor: Any) -> WhatsAppReconciliationPlan:
    sources = await executor.fetch(
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
        WHERE entity_type = 'person'
          AND metadata ->> 'merged_into' IS NULL
          AND metadata ->> 'deleted_at' IS NULL
          AND COALESCE((metadata ->> 'unidentified')::boolean, false) IS TRUE
          AND (
                metadata ->> 'source_channel' = 'whatsapp_user_client'
                OR (
                    metadata ->> 'source' = 'fact_storage'
                    AND metadata ->> 'source_butler' = 'general'
                    AND metadata ->> 'source_scope' IN ('general', 'global')
                )
          )
        ORDER BY id
        """
    )
    counts = _empty_counts()
    pairs: list[PlannedWhatsAppMerge] = []

    for source in sources:
        parsed = _parse_identifier(source["canonical_name"])
        if parsed is None:
            counts[ReconciliationCategory.INVALID_IDENTIFIER] += 1
            continue

        phone_digits = await _phone_digits_for_identifier(executor, parsed)
        if phone_digits is None:
            counts[ReconciliationCategory.UNMATCHED] += 1
            continue

        candidates = await _phone_candidates(
            executor,
            source_entity_id=source["id"],
            digits=phone_digits,
        )
        if not candidates:
            counts[ReconciliationCategory.UNMATCHED] += 1
            continue
        if len(candidates) != 1:
            counts[ReconciliationCategory.AMBIGUOUS] += 1
            continue

        target = candidates[0]
        if _owner_or_system(source) or _owner_or_system(target):
            counts[ReconciliationCategory.OWNER_OR_SYSTEM_TARGET] += 1
            continue

        review_state = await _review_state(executor, source["id"], target["id"])
        if review_state != _NO_REVIEW_DECISION:
            counts[ReconciliationCategory.EXISTING_REVIEW_DECISION] += 1
            continue

        if not _source_shell_shape_is_safe(source) or await _source_has_references(
            executor, source["id"]
        ):
            counts[ReconciliationCategory.REFERENCED_SOURCE] += 1
            continue

        pairs.append(
            PlannedWhatsAppMerge(
                source_entity_id=source["id"],
                target_entity_id=target["id"],
                source_updated_at=source["updated_at"],
                target_updated_at=target["updated_at"],
                review_state=review_state,
            )
        )
        counts[ReconciliationCategory.UNIQUE_EMPTY_SHELL] += 1

    pairs.sort(key=lambda pair: (str(pair.source_entity_id), str(pair.target_entity_id)))
    frozen_pairs = tuple(pairs)
    return WhatsAppReconciliationPlan(
        pairs=frozen_pairs,
        counts=counts,
        digest=_plan_digest(frozen_pairs),
    )


async def build_whatsapp_reconciliation_plan(
    pool: asyncpg.Pool,
) -> WhatsAppReconciliationPlan:
    """Build a write-free, aggregate-only reconciliation plan."""
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            return await _build_plan_on_executor(conn)


async def validate_empty_shell_locked(
    conn: asyncpg.Connection,
    locked_pair: LockedEntityPair,
    *,
    expected: PlannedWhatsAppMerge | None = None,
) -> None:
    """Revalidate the planned pair after deterministic entity row locks."""
    await _lock_reconciliation_relations(conn)
    source = locked_pair.source
    target = locked_pair.target
    if expected is not None and (
        source["id"] != expected.source_entity_id
        or target["id"] != expected.target_entity_id
        or source["updated_at"] != expected.source_updated_at
        or target["updated_at"] != expected.target_updated_at
        or expected.review_state != _NO_REVIEW_DECISION
    ):
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)

    parsed = _parse_identifier(source["canonical_name"])
    if parsed is None or not _source_shell_shape_is_safe(source):
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)
    phone_digits = await _phone_digits_for_identifier(conn, parsed)
    if phone_digits is None:
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)
    candidates = await _phone_candidates(
        conn,
        source_entity_id=source["id"],
        digits=phone_digits,
    )
    if len(candidates) != 1 or candidates[0]["id"] != target["id"]:
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)
    if _owner_or_system(source) or _owner_or_system(target):
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)
    if await _review_state(conn, source["id"], target["id"]) != _NO_REVIEW_DECISION:
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)
    if await _source_has_references(conn, source["id"]):
        raise LockedGuardRejected(ReconciliationCategory.PLAN_DRIFT.value)


async def _verify_postconditions(pool: asyncpg.Pool, pair: PlannedWhatsAppMerge) -> None:
    async with pool.acquire() as conn:
        source_metadata = _metadata(
            await conn.fetchval(
                "SELECT metadata FROM public.entities WHERE id = $1",
                pair.source_entity_id,
            )
        )
        if source_metadata.get("merged_into") != str(pair.target_entity_id):
            raise ReconciliationPostconditionError
        if not await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM public.entities
                WHERE id = $1
                  AND metadata ->> 'merged_into' IS NULL
                  AND metadata ->> 'deleted_at' IS NULL
            )
            """,
            pair.target_entity_id,
        ):
            raise ReconciliationPostconditionError
        if await _source_has_references(
            conn,
            pair.source_entity_id,
            allowed_review_target_id=pair.target_entity_id,
        ):
            raise ReconciliationPostconditionError
        if (
            await conn.fetchval(
                """
                SELECT count(*)
                FROM relationship.merge_reviews
                WHERE outcome = 'merged'
                  AND (
                        (entity_a = $1 AND entity_b = $2)
                        OR (entity_a = $2 AND entity_b = $1)
                  )
                """,
                pair.source_entity_id,
                pair.target_entity_id,
            )
            != 1
        ):
            raise ReconciliationPostconditionError

    fresh_plan = await build_whatsapp_reconciliation_plan(pool)
    if any(
        fresh.source_entity_id == pair.source_entity_id
        and fresh.target_entity_id == pair.target_entity_id
        for fresh in fresh_plan.pairs
    ):
        raise ReconciliationPostconditionError


def _content_blind_counts(
    counts: Mapping[ReconciliationCategory, int],
) -> dict[str, int]:
    return {category.value: int(counts[category]) for category in ReconciliationCategory}


async def apply_whatsapp_reconciliation(
    pool: asyncpg.Pool,
    *,
    authorized_digest: str,
) -> ContentBlindReconciliationReport:
    """Apply an exact current plan sequentially through the audited merge service."""
    plan = await build_whatsapp_reconciliation_plan(pool)
    if not secrets.compare_digest(plan.digest, authorized_digest):
        raise PlanDigestMismatch

    applied = 0
    for pair in plan.pairs:
        await merge_entity_pair(
            pool,
            source_entity_id=pair.source_entity_id,
            target_entity_id=pair.target_entity_id,
            locked_guard=partial(validate_empty_shell_locked, expected=pair),
        )
        await _verify_postconditions(pool, pair)
        applied += 1

    return ContentBlindReconciliationReport(
        mode="apply",
        counts=_content_blind_counts(plan.counts),
        planned=len(plan.pairs),
        applied=applied,
        plan_digest=plan.digest,
    )


__all__ = [
    "ContentBlindReconciliationReport",
    "PlanDigestMismatch",
    "PlannedWhatsAppMerge",
    "ReconciliationCategory",
    "ReconciliationPostconditionError",
    "WhatsAppReconciliationError",
    "WhatsAppReconciliationPlan",
    "apply_whatsapp_reconciliation",
    "build_whatsapp_reconciliation_plan",
    "validate_empty_shell_locked",
]
