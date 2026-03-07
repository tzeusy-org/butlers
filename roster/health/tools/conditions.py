"""Conditions and symptoms — track health conditions and log symptoms backed by SPO facts."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _normalize_end_date

logger = logging.getLogger(__name__)

VALID_CONDITION_STATUSES = {"active", "managed", "resolved"}

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


async def _get_owner_entity_id(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity's id from shared.entities."""
    try:
        row = await pool.fetchrow(
            "SELECT id FROM shared.entities WHERE 'owner' = ANY(roles) LIMIT 1"
        )
        return row["id"] if row else None
    except asyncpg.PostgresError:
        logger.debug(
            "_get_owner_entity_id: shared.entities query failed",
            exc_info=True,
        )
        return None


def _fact_to_condition(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the condition API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return {
        "id": row["id"],  # UUID — matches old DB row behaviour
        "name": meta.get("name", row.get("content", "")),
        "status": meta.get("status", "active"),
        "diagnosed_at": meta.get("diagnosed_at"),
        "notes": meta.get("notes"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }


def _fact_to_symptom(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the symptom API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    cond_id = meta.get("condition_id")
    cond_uuid = uuid.UUID(cond_id) if cond_id else None
    return {
        "id": row["id"],  # UUID — matches old DB row behaviour
        "name": row.get("content", ""),
        "severity": meta.get("severity"),
        "condition_id": cond_uuid,
        "notes": meta.get("notes"),
        "occurred_at": row.get("valid_at"),
        "created_at": row.get("created_at"),
    }


async def condition_add(
    pool: asyncpg.Pool,
    name: str,
    status: str = "active",
    diagnosed_at: datetime | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Add a health condition. Status must be one of: active, managed, resolved."""
    if status not in VALID_CONDITION_STATUSES:
        raise ValueError(
            f"Invalid condition status: {status!r}. "
            f"Must be one of: {', '.join(sorted(VALID_CONDITION_STATUSES))}"
        )

    from butlers.modules.memory.storage import store_fact

    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)

    content = f"{name}: {status}"
    metadata: dict[str, Any] = {"name": name, "status": status}
    if diagnosed_at is not None:
        metadata["diagnosed_at"] = diagnosed_at.isoformat()
    if notes is not None:
        metadata["notes"] = notes

    # Use name-keyed subject so multiple conditions coexist as independent property
    # facts. Supersession applies per (subject, predicate) key, so each condition
    # name is an independent record.
    subject = f"condition:{name}"

    fact_id = await store_fact(
        pool,
        subject=subject,
        predicate="condition",
        content=content,
        embedding_engine=embedding_engine,
        permanence="stable",
        scope="health",
        entity_id=None,  # per-name keying via subject
        valid_at=None,  # property fact — supersedes previous for same name
        metadata=metadata,
    )

    return {
        "id": fact_id,
        "name": name,
        "status": status,
        "diagnosed_at": diagnosed_at.isoformat() if diagnosed_at is not None else None,
        "notes": notes,
        "created_at": now,
        "updated_at": now,
    }


async def condition_list(
    pool: asyncpg.Pool,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List conditions, optionally filtered by status. Ordered by created_at descending."""
    conditions: list[str] = [
        "predicate = 'condition'",
        "validity = 'active'",
        "scope = 'health'",
    ]
    params: list[Any] = []
    idx = 1

    if status is not None:
        conditions.append(f"metadata->>'status' = ${idx}")
        params.append(status)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT id, predicate, content, valid_at, created_at, metadata"
        f" FROM facts {where} ORDER BY created_at DESC",
        *params,
    )
    return [_fact_to_condition(dict(r)) for r in rows]


async def condition_update(
    pool: asyncpg.Pool,
    condition_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """Update a condition. Allowed fields: name, status, diagnosed_at, notes.

    If status is provided, it must be one of: active, managed, resolved.
    Implemented as a superseding store_fact (property fact semantics).
    """
    from butlers.modules.memory.storage import store_fact

    cond_uuid = uuid.UUID(condition_id) if isinstance(condition_id, str) else condition_id
    allowed = {"name", "status", "diagnosed_at", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}

    if not updates:
        raise ValueError("No valid fields to update")

    # Validate status if provided
    if "status" in updates and updates["status"] not in VALID_CONDITION_STATUSES:
        raise ValueError(
            f"Invalid condition status: {updates['status']!r}. "
            f"Must be one of: {', '.join(sorted(VALID_CONDITION_STATUSES))}"
        )

    # Fetch existing condition fact by ID
    row = await pool.fetchrow(
        "SELECT id, subject, metadata FROM facts"
        " WHERE id = $1 AND predicate = 'condition' AND scope = 'health'",
        cond_uuid,
    )
    if row is None:
        raise ValueError(f"Condition {condition_id} not found")

    existing_meta = row["metadata"] or {}
    if isinstance(existing_meta, str):
        existing_meta = json.loads(existing_meta)
    existing_subject = row["subject"] or f"condition:{existing_meta.get('name', condition_id)}"

    # Merge updates into existing metadata
    new_meta = dict(existing_meta)
    for k, v in updates.items():
        if k == "diagnosed_at" and isinstance(v, datetime):
            new_meta[k] = v.isoformat()
        else:
            new_meta[k] = v

    name = new_meta.get("name", "")
    status = new_meta.get("status", "active")
    content = f"{name}: {status}"

    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)

    # Re-store with the same subject key to supersede the previous condition fact
    new_fact_id = await store_fact(
        pool,
        subject=existing_subject,
        predicate="condition",
        content=content,
        embedding_engine=embedding_engine,
        permanence="stable",
        scope="health",
        entity_id=None,
        valid_at=None,  # property fact — supersedes the previous
        metadata=new_meta,
    )

    return {
        "id": new_fact_id,
        "name": new_meta.get("name"),
        "status": new_meta.get("status"),
        "diagnosed_at": new_meta.get("diagnosed_at"),
        "notes": new_meta.get("notes"),
        "created_at": now,
        "updated_at": now,
    }


async def _validate_condition_fact(pool: asyncpg.Pool, condition_id: str) -> None:
    """Raise ValueError if no condition fact with this id exists."""
    cond_uuid = uuid.UUID(condition_id) if isinstance(condition_id, str) else condition_id
    row = await pool.fetchrow(
        "SELECT id FROM facts WHERE id = $1 AND predicate = 'condition' AND scope = 'health'",
        cond_uuid,
    )
    if row is None:
        raise ValueError(f"Condition {condition_id} not found")


async def symptom_log(
    pool: asyncpg.Pool,
    name: str,
    severity: int,
    condition_id: str | None = None,
    notes: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Log a symptom with severity (1-10), optionally linked to a condition."""
    if not (1 <= severity <= 10):
        raise ValueError(f"Severity must be between 1 and 10, got {severity}")

    if condition_id is not None:
        await _validate_condition_fact(pool, condition_id)

    from butlers.modules.memory.storage import store_fact

    owner_entity_id = await _get_owner_entity_id(pool)
    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)
    valid_at = occurred_at if occurred_at is not None else now

    metadata: dict[str, Any] = {"severity": severity}
    if condition_id is not None:
        metadata["condition_id"] = str(condition_id)
    if notes is not None:
        metadata["notes"] = notes

    fact_id = await store_fact(
        pool,
        subject="owner",
        predicate="symptom",
        content=name,
        embedding_engine=embedding_engine,
        permanence="standard",
        scope="health",
        entity_id=owner_entity_id,
        valid_at=valid_at,
        metadata=metadata,
    )

    cond_uuid = uuid.UUID(condition_id) if condition_id else None
    return {
        "id": fact_id,
        "name": name,
        "severity": severity,
        "condition_id": cond_uuid,
        "notes": notes,
        "occurred_at": valid_at,
        "created_at": now,
    }


async def symptom_history(
    pool: asyncpg.Pool,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get symptom history, optionally filtered by date range."""
    conditions: list[str] = [
        "predicate = 'symptom'",
        "validity = 'active'",
        "scope = 'health'",
    ]
    params: list[Any] = []
    idx = 1

    if start_date is not None:
        conditions.append(f"valid_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"valid_at <= ${idx}")
        params.append(_normalize_end_date(end_date))
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT id, predicate, content, valid_at, created_at, metadata"
        f" FROM facts {where} ORDER BY valid_at DESC",
        *params,
    )
    return [_fact_to_symptom(dict(r)) for r in rows]


async def symptom_search(
    pool: asyncpg.Pool,
    name: str | None = None,
    min_severity: int | None = None,
    max_severity: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Search symptoms by name, severity range, and date range.

    Filters are combined with AND logic. Name matching is case-insensitive.
    """
    conditions: list[str] = [
        "predicate = 'symptom'",
        "validity = 'active'",
        "scope = 'health'",
    ]
    params: list[Any] = []
    idx = 1

    if name is not None:
        conditions.append(f"content ILIKE ${idx}")
        params.append(name)
        idx += 1

    if min_severity is not None:
        conditions.append(f"(metadata->>'severity')::int >= ${idx}")
        params.append(min_severity)
        idx += 1

    if max_severity is not None:
        conditions.append(f"(metadata->>'severity')::int <= ${idx}")
        params.append(max_severity)
        idx += 1

    if start_date is not None:
        conditions.append(f"valid_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"valid_at <= ${idx}")
        params.append(_normalize_end_date(end_date))
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT id, predicate, content, valid_at, created_at, metadata"
        f" FROM facts {where} ORDER BY valid_at DESC",
        *params,
    )
    return [_fact_to_symptom(dict(r)) for r in rows]
