"""Health reports — summary and trend reports backed by SPO facts."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

VALID_TREND_PERIODS = {"week", "month"}

VALID_MEASUREMENT_TYPES = {"weight", "blood_pressure", "heart_rate", "blood_sugar", "temperature"}


async def health_summary(pool: asyncpg.Pool) -> dict[str, Any]:
    """Get a health overview: latest measurements, active medications, conditions."""
    # Latest measurement fact per type (most recent valid_at per measurement predicate)
    recent_measurements: list[dict[str, Any]] = []
    for mtype in VALID_MEASUREMENT_TYPES:
        predicate = f"measurement_{mtype}"
        row = await pool.fetchrow(
            "SELECT id, predicate, content, valid_at, created_at, metadata"
            " FROM facts"
            " WHERE predicate = $1 AND validity = 'active' AND scope = 'health'"
            " ORDER BY valid_at DESC NULLS LAST LIMIT 1",
            predicate,
        )
        if row is not None:
            meta = row["metadata"] or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            recent_measurements.append(
                {
                    "id": str(row["id"]),
                    "type": mtype,
                    "value": meta.get("value"),
                    "notes": meta.get("notes"),
                    "measured_at": row["valid_at"],
                    "created_at": row["created_at"],
                }
            )

    # Active medications (active=true property facts)
    med_rows = await pool.fetch(
        "SELECT id, predicate, content, valid_at, created_at, metadata"
        " FROM facts"
        " WHERE predicate = 'medication' AND validity = 'active' AND scope = 'health'"
        " AND (metadata->>'active')::boolean = true"
        " ORDER BY metadata->>'name'"
    )
    active_medications: list[dict[str, Any]] = []
    for row in med_rows:
        meta = row["metadata"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        active_medications.append(
            {
                "id": str(row["id"]),
                "name": meta.get("name", ""),
                "dosage": meta.get("dosage", ""),
                "frequency": meta.get("frequency", ""),
                "schedule": meta.get("schedule", []),
                "active": meta.get("active", True),
                "notes": meta.get("notes"),
                "created_at": row["created_at"],
            }
        )

    # Active conditions (status='active' property facts)
    cond_rows = await pool.fetch(
        "SELECT id, predicate, content, valid_at, created_at, metadata"
        " FROM facts"
        " WHERE predicate = 'condition' AND validity = 'active' AND scope = 'health'"
        " AND metadata->>'status' = 'active'"
        " ORDER BY metadata->>'name'"
    )
    active_conditions: list[dict[str, Any]] = []
    for row in cond_rows:
        meta = row["metadata"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        active_conditions.append(
            {
                "id": str(row["id"]),
                "name": meta.get("name", row["content"]),
                "status": meta.get("status", "active"),
                "diagnosed_at": meta.get("diagnosed_at"),
                "notes": meta.get("notes"),
                "created_at": row["created_at"],
            }
        )

    return {
        "recent_measurements": recent_measurements,
        "active_medications": active_medications,
        "active_conditions": active_conditions,
    }


async def trend_report(
    pool: asyncpg.Pool,
    period: str = "week",
) -> dict[str, Any]:
    """Generate a trend report over a period (week=7d, month=30d).

    Returns measurement trends (grouped by type with first/last values),
    medication adherence rates, symptom frequency, and symptom severity averages.
    """
    if period not in VALID_TREND_PERIODS:
        raise ValueError(
            f"Invalid period: {period!r}. Must be one of: {', '.join(sorted(VALID_TREND_PERIODS))}"
        )

    days = 7 if period == "week" else 30
    now = datetime.now(UTC)
    since = now - timedelta(days=days)

    # Measurement trends grouped by type
    measurement_trends: dict[str, Any] = {}
    for mtype in VALID_MEASUREMENT_TYPES:
        predicate = f"measurement_{mtype}"
        meas_rows = await pool.fetch(
            "SELECT id, predicate, content, valid_at, created_at, metadata"
            " FROM facts"
            " WHERE predicate = $1 AND validity = 'active' AND scope = 'health'"
            " AND valid_at >= $2"
            " ORDER BY valid_at ASC",
            predicate,
            since,
        )
        if not meas_rows:
            continue

        entries = []
        for row in meas_rows:
            meta = row["metadata"] or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            entry = {
                "id": str(row["id"]),
                "type": mtype,
                "value": meta.get("value"),
                "notes": meta.get("notes"),
                "measured_at": row["valid_at"],
                "created_at": row["created_at"],
            }
            entries.append(entry)

        measurement_trends[mtype] = {
            "measurements": entries,
            "first": entries[0],
            "last": entries[-1],
        }

    # Medication adherence — based on took_dose facts
    med_rows = await pool.fetch(
        "SELECT id, metadata FROM facts"
        " WHERE predicate = 'medication' AND validity = 'active' AND scope = 'health'"
        " AND (metadata->>'active')::boolean = true"
    )
    medication_adherence: list[dict[str, Any]] = []
    for med in med_rows:
        meta = med["metadata"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        med_id_str = str(med["id"])
        med_name = meta.get("name", med_id_str)

        dose_rows = await pool.fetch(
            "SELECT metadata->>'skipped' AS skipped"
            " FROM facts"
            " WHERE predicate = 'took_dose'"
            " AND validity = 'active'"
            " AND scope = 'health'"
            " AND metadata->>'medication_id' = $1"
            " AND valid_at >= $2",
            med_id_str,
            since,
        )
        total = len(dose_rows)
        taken = sum(1 for d in dose_rows if d["skipped"] not in (True, "true", "True", "1"))
        rate = round(taken / total * 100, 1) if total > 0 else None
        medication_adherence.append(
            {
                "medication_id": med["id"],
                "name": med_name,
                "total_doses": total,
                "taken_doses": taken,
                "adherence_rate": rate,
            }
        )

    # Symptom frequency and severity
    sym_rows = await pool.fetch(
        "SELECT content, (metadata->>'severity')::int AS severity"
        " FROM facts"
        " WHERE predicate = 'symptom'"
        " AND validity = 'active'"
        " AND scope = 'health'"
        " AND valid_at >= $1",
        since,
    )
    sym_freq: dict[str, int] = {}
    sym_sev: dict[str, list[int]] = {}
    for row in sym_rows:
        n = row["content"]
        sev = row["severity"]
        sym_freq[n] = sym_freq.get(n, 0) + 1
        if sev is not None:
            sym_sev.setdefault(n, []).append(sev)

    symptom_severity_avg = {n: round(sum(sevs) / len(sevs), 1) for n, sevs in sym_sev.items()}

    return {
        "period": period,
        "days": days,
        "measurement_trends": measurement_trends,
        "medication_adherence": medication_adherence,
        "symptom_frequency": sym_freq,
        "symptom_severity_avg": symptom_severity_avg,
    }
