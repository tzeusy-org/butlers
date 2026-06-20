"""Health measurements — log, query, and retrieve latest measurements backed by temporal facts."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _get_owner_entity_id, _normalize_end_date

logger = logging.getLogger(__name__)

VALID_MEASUREMENT_TYPES = {"weight", "blood_pressure", "heart_rate", "blood_sugar", "temperature"}

# Notes substrings that indicate a call originated from butler-generated
# digest/briefing/summary output rather than a genuine user measurement.
# Used by _is_passive_provenance() to block circular re-ingestion.
_PASSIVE_PROVENANCE_MARKERS: frozenset[str] = frozenset(
    {
        "briefing",
        "digest",
        "passive",
        "daily summary",
        "weekly summary",
        "health summary",
        "trend report",
    }
)


def _is_passive_provenance(notes: str | None) -> bool:
    """Return True if notes text signals digest/briefing/passive provenance.

    Measurements must come only from explicit user statements or structured
    wellness ingestion — never from re-reading butler-generated summaries.
    """
    if not notes:
        return False
    notes_lower = notes.lower()
    return any(marker in notes_lower for marker in _PASSIVE_PROVENANCE_MARKERS)


_MEASUREMENT_UNITS: dict[str, str] = {
    "weight": "kg",
    "blood_pressure": "mmHg",
    "heart_rate": "bpm",
    "blood_sugar": "mg/dL",
    "temperature": "°C",
}

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _fact_to_measurement(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the measurement API shape."""
    predicate = row.get("predicate", "")
    # e.g. "measurement_blood_pressure" -> "blood_pressure"
    mtype = predicate.removeprefix("measurement_")
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    value = meta.get("value") if isinstance(meta, dict) else None
    notes = meta.get("notes") if isinstance(meta, dict) else None
    return {
        "id": row["id"],  # UUID — matches old DB row behaviour
        "type": mtype,
        "value": value,
        "notes": notes,
        "measured_at": row.get("valid_at"),
        "created_at": row.get("created_at"),
    }


async def measurement_log(
    pool: asyncpg.Pool,
    type: str,
    value: Any,
    notes: str | None = None,
    measured_at: datetime | None = None,
) -> dict[str, Any]:
    """Log a health measurement. Value is stored as JSONB for compound values.

    The type parameter must be one of: weight, blood_pressure, heart_rate,
    blood_sugar, temperature.
    """
    if type not in VALID_MEASUREMENT_TYPES:
        raise ValueError(
            f"Unrecognized measurement type: {type!r}. "
            f"Must be one of: {', '.join(sorted(VALID_MEASUREMENT_TYPES))}"
        )

    if _is_passive_provenance(notes):
        raise ValueError(
            "measurement_log rejected: notes indicate digest/briefing/passive-telegram "
            "provenance. Measurements must originate from explicit user statements or "
            "structured wellness ingestion only — never from butler-generated summaries."
        )

    from butlers.modules.memory.storage import store_fact

    predicate = f"measurement_{type}"
    owner_entity_id = await _get_owner_entity_id(pool)
    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)
    valid_at = measured_at if measured_at is not None else now

    # Build human-readable content summary
    if isinstance(value, dict):
        parts = [f"{k}={v}" for k, v in value.items()]
        content = f"{type}: {', '.join(parts)}"
    else:
        unit = _MEASUREMENT_UNITS.get(type, "")
        content = f"{type}: {value}{(' ' + unit) if unit else ''}"

    metadata: dict[str, Any] = {"value": value}
    if notes is not None:
        metadata["notes"] = notes

    fact_id = (
        await store_fact(
            pool,
            subject="owner",
            predicate=predicate,
            content=content,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="health",
            entity_id=owner_entity_id,
            valid_at=valid_at,
            metadata=metadata,
        )
    )["id"]

    return {
        "id": fact_id,
        "type": type,
        "value": value,
        "notes": notes,
        "measured_at": valid_at,
        "created_at": now,
    }


async def measurement_update(
    pool: asyncpg.Pool,
    measurement_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """Update a logged measurement. Allowed fields: type, value, notes, measured_at.

    Measurements are TEMPORAL facts (``valid_at`` is the reading time and
    supersession is skipped for temporal facts), so — like symptoms and meals —
    there is no superseding ``store_fact`` path keyed on (subject, predicate).
    Re-storing would create a *second* coexisting reading rather than replacing
    the first. Instead this performs an in-place UPDATE of the existing
    measurement fact row so the edit preserves the same identity and the
    temporal log keeps exactly one entry per logged reading.

    Changing ``type`` rewrites the predicate to ``measurement_{type}`` (the
    measurement type is encoded in the predicate, not in metadata) and must be
    one of the recognized measurement types. ``value`` is stored in the
    ``value`` metadata field (scalar or compound dict). The ``facts`` table has
    no ``updated_at`` column, so only ``predicate``, ``content``, ``metadata``,
    and ``valid_at`` are touched.
    """
    meas_uuid = uuid.UUID(measurement_id) if isinstance(measurement_id, str) else measurement_id
    allowed = {"type", "value", "notes", "measured_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}

    if not updates:
        raise ValueError("No valid fields to update")

    if "type" in updates and updates["type"] not in VALID_MEASUREMENT_TYPES:
        raise ValueError(
            f"Unrecognized measurement type: {updates['type']!r}. "
            f"Must be one of: {', '.join(sorted(VALID_MEASUREMENT_TYPES))}"
        )

    row = await pool.fetchrow(
        "SELECT id, predicate, content, valid_at, created_at, metadata FROM facts"
        " WHERE id = $1 AND predicate LIKE 'measurement~_%' ESCAPE '~'"
        " AND scope = 'health' AND validity = 'active'",
        meas_uuid,
    )
    if row is None:
        raise ValueError(f"Measurement {measurement_id} not found")

    existing_meta = row["metadata"] or {}
    if isinstance(existing_meta, str):
        existing_meta = json.loads(existing_meta)
    new_meta = dict(existing_meta)

    if "value" in updates:
        new_meta["value"] = updates["value"]
    if "notes" in updates:
        if updates["notes"] is None:
            new_meta.pop("notes", None)
        else:
            new_meta["notes"] = updates["notes"]

    mtype = updates["type"] if "type" in updates else row["predicate"].removeprefix("measurement_")
    predicate = f"measurement_{mtype}"
    valid_at = updates.get("measured_at", row["valid_at"])
    value = new_meta.get("value")

    # Rebuild the human-readable content summary to match measurement_log.
    if isinstance(value, dict):
        parts = [f"{k}={v}" for k, v in value.items()]
        content = f"{mtype}: {', '.join(parts)}"
    else:
        unit = _MEASUREMENT_UNITS.get(mtype, "")
        content = f"{mtype}: {value}{(' ' + unit) if unit else ''}"

    await pool.execute(
        "UPDATE facts SET predicate = $2, content = $3, metadata = $4, valid_at = $5 WHERE id = $1",
        meas_uuid,
        predicate,
        content,
        new_meta,
        valid_at,
    )

    return _fact_to_measurement(
        {
            "id": meas_uuid,
            "predicate": predicate,
            "content": content,
            "valid_at": valid_at,
            "created_at": row["created_at"],
            "metadata": new_meta,
        }
    )


async def measurement_delete(
    pool: asyncpg.Pool,
    measurement_id: str,
) -> bool:
    """Soft-delete a logged measurement by retracting its fact.

    Delegates to ``forget_memory(pool, "fact", id)``, which sets the fact's
    ``validity`` to ``'retracted'`` — the canonical soft-delete path used across
    the memory subsystem. The fact remains in the database for audit but is
    excluded from all ``validity = 'active'`` read surfaces (including the
    dashboard GET endpoint and ``measurement_history`` / ``measurement_latest``).
    Raises ``ValueError`` if no active measurement fact with this id exists.
    """
    from butlers.modules.memory.storage import forget_memory

    meas_uuid = uuid.UUID(measurement_id) if isinstance(measurement_id, str) else measurement_id

    row = await pool.fetchrow(
        "SELECT id FROM facts"
        " WHERE id = $1 AND predicate LIKE 'measurement~_%' ESCAPE '~'"
        " AND scope = 'health' AND validity = 'active'",
        meas_uuid,
    )
    if row is None:
        raise ValueError(f"Measurement {measurement_id} not found")

    return await forget_memory(pool, "fact", meas_uuid)


async def measurement_history(
    pool: asyncpg.Pool,
    type: str,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get measurement history for a type, optionally filtered by date range."""
    if type not in VALID_MEASUREMENT_TYPES:
        raise ValueError(
            f"Unrecognized measurement type: {type!r}. "
            f"Must be one of: {', '.join(sorted(VALID_MEASUREMENT_TYPES))}"
        )

    predicate = f"measurement_{type}"
    conditions = ["predicate = $1", "validity = 'active'", "scope = 'health'"]
    params: list[Any] = [predicate]
    idx = 2

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
    return [_fact_to_measurement(dict(r)) for r in rows]


async def measurement_latest(
    pool: asyncpg.Pool,
    type: str,
) -> dict[str, Any] | None:
    """Get the most recent measurement for a type."""
    if type not in VALID_MEASUREMENT_TYPES:
        raise ValueError(
            f"Unrecognized measurement type: {type!r}. "
            f"Must be one of: {', '.join(sorted(VALID_MEASUREMENT_TYPES))}"
        )

    predicate = f"measurement_{type}"
    row = await pool.fetchrow(
        "SELECT id, predicate, content, valid_at, created_at, metadata"
        " FROM facts"
        " WHERE predicate = $1 AND validity = 'active' AND scope = 'health'"
        " ORDER BY valid_at DESC LIMIT 1",
        predicate,
    )
    if row is None:
        return None
    return _fact_to_measurement(dict(row))
