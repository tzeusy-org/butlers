"""Medications — add, list, log doses, and view adherence history backed by SPO facts."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _get_owner_entity_id, _normalize_end_date

logger = logging.getLogger(__name__)

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _fact_to_medication(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the medication API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return {
        "id": row["id"],  # UUID — matches old DB row behaviour
        "name": meta.get("name", ""),
        "dosage": meta.get("dosage", ""),
        "frequency": meta.get("frequency", ""),
        "schedule": meta.get("schedule", []),
        "active": meta.get("active", True),
        "notes": meta.get("notes"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }


def _fact_to_dose(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the dose API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    med_id = meta.get("medication_id")
    med_uuid = uuid.UUID(med_id) if med_id else None
    return {
        "id": str(row["id"]),
        "medication_id": med_uuid,
        "skipped": meta.get("skipped", False),
        "notes": meta.get("notes"),
        "taken_at": row.get("valid_at"),
        "created_at": row.get("created_at"),
    }


async def medication_add(
    pool: asyncpg.Pool,
    name: str,
    dosage: str,
    frequency: str,
    schedule: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Add a medication with dosage, frequency, optional schedule and notes."""
    from butlers.modules.memory.storage import store_fact

    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)

    content = f"{name} {dosage} {frequency}"
    metadata: dict[str, Any] = {
        "name": name,
        "dosage": dosage,
        "frequency": frequency,
        "schedule": schedule or [],
        "active": True,
    }
    if notes is not None:
        metadata["notes"] = notes

    # Use a name-keyed subject so multiple medications coexist as independent
    # property facts. Supersession is keyed on (subject, predicate) ONLY when
    # entity_id is omitted — when an entity_id is passed, store_fact keys
    # supersession on (entity_id, scope, predicate) and IGNORES the subject,
    # which would make every medication for the owner collide on
    # (owner, health, medication) and silently supersede the previous one.
    # Therefore do NOT anchor these per-item facts to the owner entity; the
    # name-keyed subject is the correct supersession key (re-adding the same
    # name supersedes; distinct names coexist).
    subject = f"medication:{name}"

    fact_id = (
        await store_fact(
            pool,
            subject=subject,
            predicate="medication",
            content=content,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="health",
            valid_at=None,  # property fact — supersedes previous for same name
            metadata=metadata,
        )
    )["id"]

    return {
        "id": fact_id,
        "name": name,
        "dosage": dosage,
        "frequency": frequency,
        "schedule": schedule or [],
        "active": True,
        "notes": notes,
        "created_at": now,
        "updated_at": now,
    }


async def medication_update(
    pool: asyncpg.Pool,
    medication_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """Update a medication. Allowed fields: name, dosage, frequency, schedule, active, notes.

    Implemented as a superseding ``store_fact`` (property-fact semantics): the
    existing medication fact is looked up by ``id``, its metadata is merged with
    the supplied fields, and a new fact is written with the same subject key so
    the previous fact is superseded.  This is the same write path the butler's
    own tools use, so dashboard edits and butler edits are indistinguishable.
    """
    from butlers.modules.memory.storage import store_fact

    med_uuid = uuid.UUID(medication_id) if isinstance(medication_id, str) else medication_id
    allowed = {"name", "dosage", "frequency", "schedule", "active", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}

    if not updates:
        raise ValueError("No valid fields to update")

    # Fetch the existing medication fact by ID.
    row = await pool.fetchrow(
        "SELECT id, subject, metadata FROM facts"
        " WHERE id = $1 AND predicate = 'medication' AND scope = 'health'",
        med_uuid,
    )
    if row is None:
        raise ValueError(f"Medication {medication_id} not found")

    existing_meta = row["metadata"] or {}
    if isinstance(existing_meta, str):
        existing_meta = json.loads(existing_meta)
    existing_subject = row["subject"] or f"medication:{existing_meta.get('name', medication_id)}"

    # Merge updates into existing metadata.
    new_meta = dict(existing_meta)
    new_meta.update(updates)

    name = new_meta.get("name", "")
    dosage = new_meta.get("dosage", "")
    frequency = new_meta.get("frequency", "")
    content = f"{name} {dosage} {frequency}".strip()

    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)

    # Re-store with the same subject key to supersede the previous medication
    # fact. Do NOT pass entity_id: supersession must key on (subject, predicate)
    # so the edit only supersedes THIS medication's prior fact, not every other
    # medication anchored to the owner entity (see medication_add for details).
    new_fact_id = (
        await store_fact(
            pool,
            subject=existing_subject,
            predicate="medication",
            content=content,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="health",
            valid_at=None,  # property fact — supersedes the previous
            metadata=new_meta,
        )
    )["id"]

    return {
        "id": new_fact_id,
        "name": new_meta.get("name", ""),
        "dosage": new_meta.get("dosage", ""),
        "frequency": new_meta.get("frequency", ""),
        "schedule": list(new_meta.get("schedule") or []),
        "active": bool(new_meta.get("active", True)),
        "notes": new_meta.get("notes"),
        "created_at": now,
        "updated_at": now,
    }


async def medication_delete(
    pool: asyncpg.Pool,
    medication_id: str,
) -> bool:
    """Soft-delete a medication by retracting its fact.

    Delegates to ``forget_memory(pool, "fact", id)``, which sets the fact's
    ``validity`` to ``'retracted'`` — the canonical soft-delete path used across
    the memory subsystem.  The fact remains in the database for audit but is
    excluded from all ``validity = 'active'`` read surfaces (including the
    dashboard GET endpoints).  Raises ``ValueError`` if no active medication
    fact with this id exists.
    """
    from butlers.modules.memory.storage import forget_memory

    med_uuid = uuid.UUID(medication_id) if isinstance(medication_id, str) else medication_id

    row = await pool.fetchrow(
        "SELECT id FROM facts"
        " WHERE id = $1 AND predicate = 'medication' AND scope = 'health'"
        " AND validity = 'active'",
        med_uuid,
    )
    if row is None:
        raise ValueError(f"Medication {medication_id} not found")

    return await forget_memory(pool, "fact", med_uuid)


async def medication_list(
    pool: asyncpg.Pool,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """List medications, optionally only active ones."""
    if active_only:
        rows = await pool.fetch(
            "SELECT id, predicate, content, valid_at, created_at, metadata"
            " FROM facts"
            " WHERE predicate = 'medication' AND validity = 'active' AND scope = 'health'"
            " AND (metadata->>'active')::boolean = true"
            " ORDER BY metadata->>'name'"
        )
    else:
        rows = await pool.fetch(
            "SELECT id, predicate, content, valid_at, created_at, metadata"
            " FROM facts"
            " WHERE predicate = 'medication' AND validity = 'active' AND scope = 'health'"
            " ORDER BY metadata->>'name'"
        )
    return [_fact_to_medication(dict(r)) for r in rows]


async def medication_log_dose(
    pool: asyncpg.Pool,
    medication_id: str,
    taken_at: datetime | None = None,
    skipped: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """Log a medication dose. Use skipped=True to record a missed dose."""
    from butlers.modules.memory.storage import store_fact

    med_uuid = uuid.UUID(medication_id) if isinstance(medication_id, str) else medication_id

    # Validate medication fact exists
    med_row = await pool.fetchrow(
        "SELECT id, metadata FROM facts"
        " WHERE id = $1 AND predicate = 'medication' AND scope = 'health'",
        med_uuid,
    )
    if med_row is None:
        raise ValueError(f"Medication {medication_id} not found")

    med_meta = med_row["metadata"] or {}
    if isinstance(med_meta, str):
        med_meta = json.loads(med_meta)
    med_name = med_meta.get("name", str(medication_id))

    owner_entity_id = await _get_owner_entity_id(pool)
    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)
    valid_at = taken_at if taken_at is not None else now

    metadata: dict[str, Any] = {
        "medication_id": str(med_uuid),
        "skipped": skipped,
    }
    if notes is not None:
        metadata["notes"] = notes

    fact_id = (
        await store_fact(
            pool,
            subject="owner",
            predicate="took_dose",
            content=med_name,
            embedding_engine=embedding_engine,
            permanence="standard",
            scope="health",
            entity_id=owner_entity_id,
            valid_at=valid_at,
            metadata=metadata,
        )
    )["id"]

    return {
        "id": fact_id,
        "medication_id": med_uuid,
        "skipped": skipped,
        "notes": notes,
        "taken_at": valid_at,
        "created_at": now,
    }


async def medication_history(
    pool: asyncpg.Pool,
    medication_id: str,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict[str, Any]:
    """Get medication dose history with adherence rate.

    Adherence rate is the percentage of non-skipped doses out of total logged doses.
    Returns null for adherence_rate if no doses exist.
    """
    med_uuid = uuid.UUID(medication_id) if isinstance(medication_id, str) else medication_id

    # Get medication info (the current active fact)
    med_row = await pool.fetchrow(
        "SELECT id, metadata, created_at FROM facts"
        " WHERE id = $1 AND predicate = 'medication' AND scope = 'health'",
        med_uuid,
    )
    if med_row is None:
        raise ValueError(f"Medication {medication_id} not found")

    medication = _fact_to_medication(dict(med_row))

    # Build dose query — filter on medication_id in metadata
    conditions = [
        "predicate = 'took_dose'",
        "validity = 'active'",
        "scope = 'health'",
        f"metadata->>'medication_id' = '{med_uuid}'",
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
    dose_rows = await pool.fetch(
        f"SELECT id, predicate, content, valid_at, created_at, metadata"
        f" FROM facts {where} ORDER BY valid_at DESC",
        *params,
    )
    doses = [_fact_to_dose(dict(r)) for r in dose_rows]

    # Calculate adherence rate: percentage of non-skipped doses
    adherence_rate = None
    if doses:
        taken_count = sum(1 for d in doses if not d.get("skipped", False))
        adherence_rate = round(taken_count / len(doses) * 100, 1)

    return {
        "medication": medication,
        "doses": doses,
        "adherence_rate": adherence_rate,
    }
