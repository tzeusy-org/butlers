"""Medications — add, list, log doses, and view adherence history."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _normalize_end_date, _row_to_dict

logger = logging.getLogger(__name__)


async def medication_add(
    pool: asyncpg.Pool,
    name: str,
    dosage: str,
    frequency: str,
    schedule: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Add a medication with dosage, frequency, optional schedule and notes."""
    row = await pool.fetchrow(
        """
        INSERT INTO medications (name, dosage, frequency, schedule, notes)
        VALUES ($1, $2, $3, $4::jsonb, $5)
        RETURNING *
        """,
        name,
        dosage,
        frequency,
        json.dumps(schedule or []),
        notes,
    )
    return _row_to_dict(row)


async def medication_list(
    pool: asyncpg.Pool,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """List medications, optionally only active ones."""
    if active_only:
        rows = await pool.fetch("SELECT * FROM medications WHERE active = true ORDER BY name")
    else:
        rows = await pool.fetch("SELECT * FROM medications ORDER BY name")
    return [_row_to_dict(r) for r in rows]


async def medication_log_dose(
    pool: asyncpg.Pool,
    medication_id: str,
    taken_at: datetime | None = None,
    skipped: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """Log a medication dose. Use skipped=True to record a missed dose."""
    med_uuid = uuid.UUID(medication_id) if isinstance(medication_id, str) else medication_id

    # Validate medication exists
    med = await pool.fetchrow("SELECT id FROM medications WHERE id = $1", med_uuid)
    if med is None:
        raise ValueError(f"Medication {medication_id} not found")

    row = await pool.fetchrow(
        """
        INSERT INTO medication_doses (medication_id, taken_at, skipped, notes)
        VALUES ($1, COALESCE($2, now()), $3, $4)
        RETURNING *
        """,
        med_uuid,
        taken_at,
        skipped,
        notes,
    )
    return _row_to_dict(row)


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

    # Get medication info
    med_row = await pool.fetchrow("SELECT * FROM medications WHERE id = $1", med_uuid)
    if med_row is None:
        raise ValueError(f"Medication {medication_id} not found")

    # Build dose query
    conditions = ["medication_id = $1"]
    params: list[Any] = [med_uuid]
    idx = 2

    if start_date is not None:
        conditions.append(f"taken_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"taken_at <= ${idx}")
        params.append(_normalize_end_date(end_date))
        idx += 1

    where = " AND ".join(conditions)
    dose_rows = await pool.fetch(
        f"SELECT * FROM medication_doses WHERE {where} ORDER BY taken_at DESC",
        *params,
    )
    doses = [_row_to_dict(r) for r in dose_rows]

    # Calculate adherence rate: percentage of non-skipped doses
    adherence_rate = None
    if doses:
        taken_count = sum(1 for d in doses if not d.get("skipped", False))
        adherence_rate = round(taken_count / len(doses) * 100, 1)

    return {
        "medication": _row_to_dict(med_row),
        "doses": doses,
        "adherence_rate": adherence_rate,
    }
