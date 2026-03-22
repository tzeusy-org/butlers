"""Health measurements — log, query, and retrieve latest measurements backed by temporal facts."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _normalize_end_date

logger = logging.getLogger(__name__)

VALID_MEASUREMENT_TYPES = {"weight", "blood_pressure", "heart_rate", "blood_sugar", "temperature"}

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


async def _get_owner_entity_id(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity's id from shared.entities."""
    try:
        row = await pool.fetchrow(
            "SELECT id FROM shared.entities WHERE 'owner' = ANY(roles) LIMIT 1"
        )
        return row["id"] if row else None
    except asyncpg.PostgresError:
        logger.debug(
            "_get_owner_entity_id: shared.entities query failed (table may not exist yet)",
            exc_info=True,
        )
        return None


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
