"""Health measurements — log, query, and retrieve latest measurements."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _normalize_end_date, _row_to_dict

logger = logging.getLogger(__name__)

VALID_MEASUREMENT_TYPES = {"weight", "blood_pressure", "heart_rate", "blood_sugar", "temperature"}


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
    row = await pool.fetchrow(
        """
        INSERT INTO measurements (type, value, notes, measured_at)
        VALUES ($1, $2::jsonb, $3, COALESCE($4, now()))
        RETURNING *
        """,
        type,
        json.dumps(value),
        notes,
        measured_at,
    )
    return _row_to_dict(row)


async def measurement_history(
    pool: asyncpg.Pool,
    type: str,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get measurement history for a type, optionally filtered by date range."""
    conditions = ["type = $1"]
    params: list[Any] = [type]
    idx = 2

    if start_date is not None:
        conditions.append(f"measured_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"measured_at <= ${idx}")
        params.append(_normalize_end_date(end_date))
        idx += 1

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT * FROM measurements WHERE {where} ORDER BY measured_at DESC",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def measurement_latest(
    pool: asyncpg.Pool,
    type: str,
) -> dict[str, Any] | None:
    """Get the most recent measurement for a type."""
    row = await pool.fetchrow(
        "SELECT * FROM measurements WHERE type = $1 ORDER BY measured_at DESC LIMIT 1",
        type,
    )
    if row is None:
        return None
    return _row_to_dict(row)
