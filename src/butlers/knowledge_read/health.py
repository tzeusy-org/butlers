"""Health readers over published tables: series, profile and records (RFC 0039 D10, OD17).

``health_series`` reads ``health.measurements_public`` (numeric readings only, as the source
materialization admits); ``health_profile`` reads the typed medication and condition tables and
the allergy and provider facts. A reading's unit is never converted.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.knowledge_read.core import (
    Arguments,
    ReadContext,
    ReadError,
    fields,
    fit,
    in_snapshot,
    published,
    record_uri,
    span,
)
from butlers.knowledge_read.records import json_fields, read_records

SERIES_MAX_DAYS = 366
SERIES_POINTS = 2000
PROFILE_LIMIT = 200
SECTIONS = ("medications", "conditions", "allergies", "providers")
ALLERGY_PREDICATES = ("allergy", "food_allergy")
PROVIDER_PREDICATES = ("doctor_name", "doctor_primary_care", "pharmacy")


async def health_series(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Numeric readings of one metric over UTC days ``start``..``end``.

    ``day`` and ``week`` (Monday) buckets return the arithmetic mean in the unit unchanged. If
    the window mixes units, only the newest reading's unit is served and the others are listed
    in ``units_omitted`` (absent otherwise). More than 2,000 points refuse with ``too_large``.
    """
    args = Arguments(arguments, {"metric", "start", "end", "bucket"})
    metric = args.text("metric", max_length=128, required=True)
    start, end = args.day("start"), args.day("end")
    span(start, end, max_days=SERIES_MAX_DAYS)
    bucket = args.choice("bucket", ("raw", "day", "week"))
    context.require("health")
    _, view = published("health.measurements_public", "health_series")
    lower = datetime.combine(start, time(), UTC)
    upper = datetime.combine(end + timedelta(days=1), time(), UTC)
    window = """fields->>'metric' = $1
        AND (fields->>'measured_at')::timestamptz >= $2
        AND (fields->>'measured_at')::timestamptz < $3"""
    if bucket == "raw":
        select = """(fields->>'measured_at')::timestamptz AS t,
            fields->>'value_numeric' AS value, fields->>'unit' AS unit"""
        tail = "ORDER BY t, source_id"
    else:
        select = """date_trunc($5::text, (fields->>'measured_at')::timestamptz AT TIME ZONE 'UTC')
            ::date AS t, avg((fields->>'value_numeric')::numeric)::text AS value,
            min(fields->>'unit') AS unit"""
        tail = "GROUP BY 1 ORDER BY 1"

    async def read() -> tuple[list[asyncpg.Record], list[asyncpg.Record]]:
        metrics = await conn.fetch(
            f"SELECT DISTINCT fields->>'metric' AS metric FROM {view} ORDER BY 1"
        )
        if metric not in {m["metric"] for m in metrics}:
            raise ReadError("invalid_argument", argument="metric")
        units = await conn.fetch(
            f"""SELECT fields->>'unit' AS unit,
                max((fields->>'measured_at')::timestamptz) AS latest
            FROM {view} WHERE {window} GROUP BY 1 ORDER BY latest DESC, 1""",
            metric,
            lower,
            upper,
        )
        if not units:
            return [], []
        extra = () if bucket == "raw" else (bucket,)
        rows = await conn.fetch(
            f"SELECT {select} FROM {view} WHERE {window} "
            f"AND COALESCE(fields->>'unit', '') = $4::text {tail} LIMIT {SERIES_POINTS + 1}",
            metric,
            lower,
            upper,
            units[0]["unit"] or "",
            *extra,
        )
        return rows, units

    rows, units = await in_snapshot(conn, read)
    if len(rows) > SERIES_POINTS:
        raise ReadError("too_large", domain="health")
    points = [{"t": r["t"].isoformat(), "value": float(Decimal(r["value"]))} for r in rows]
    omitted = [u["unit"] for u in units[1:]]
    source = context.aggregate_source("health", "health_series")

    def build(kept: list, _: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "unit": units[0]["unit"] if units else None,
            "points": kept,
            "source": source,
        }
        if omitted:
            result["units_omitted"] = omitted
        return result

    return fit(points, build)


def _text(value: Any) -> Any:
    return value if value not in ("", None) else None


async def health_profile(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Medications, conditions, allergies and providers, each capped at 200 entries."""
    args = Arguments(arguments, {"sections"})
    chosen = args.subset("sections", max_items=4, max_length=16, options=SECTIONS) or SECTIONS
    context.require("health")
    medications, med_view = published("health.medications_public", "health_profile")
    conditions, cond_view = published("health.conditions_public", "health_profile")
    facts, fact_view = published("health.facts", "health_profile")
    cap = PROFILE_LIMIT + 1

    async def facts_of(predicates: tuple[str, ...]) -> list[asyncpg.Record]:
        return await conn.fetch(
            f"""SELECT source_id, fields FROM {fact_view}
            WHERE fields->>'predicate' = ANY($1::text[])
            ORDER BY (fields->>'created_at')::timestamptz DESC, source_id LIMIT $2""",
            list(predicates),
            cap,
        )

    async def read() -> dict[str, Any]:
        found: dict[str, Any] = {}
        if "medications" in chosen:
            found["medications"] = await conn.fetch(
                f"""SELECT source_id, fields FROM {med_view}
                ORDER BY (fields->>'recorded_at')::timestamptz DESC NULLS LAST, source_id
                LIMIT $1""",
                cap,
            )
        if "conditions" in chosen:
            found["conditions"] = await conn.fetch(
                f"""SELECT source_id, fields FROM {cond_view}
                ORDER BY fields->>'name', source_id LIMIT $1""",
                cap,
            )
        if "allergies" in chosen:
            found["allergies"] = await facts_of(ALLERGY_PREDICATES)
        if "providers" in chosen:
            found["providers"] = await facts_of(PROVIDER_PREDICATES)
        ids = [
            r["source_id"]
            for k in ("allergies", "providers")
            for r in found.get(k, [])[:PROFILE_LIMIT]
        ]
        found["json"] = await json_fields(conn, "health", facts.source, ids, "health_profile")
        return found

    found = await in_snapshot(conn, read)
    entries: list[tuple[str, dict[str, Any]]] = []
    cut = False
    for section, table in (
        ("medications", medications),
        ("conditions", conditions),
        ("allergies", facts),
        ("providers", facts),
    ):
        rows = found.get(section, [])
        cut = cut or len(rows) > PROFILE_LIMIT
        for row in rows[:PROFILE_LIMIT]:
            data = fields(row["fields"])
            kind, uri = record_uri(table, row["source_id"])
            source = context.source(kind, uri, data.get("content_authority"))
            if section == "medications":
                active = data.get("active")
                entry = {
                    "name": data.get("name"),
                    "dose": data.get("dosage"),
                    "frequency": data.get("frequency"),
                    "schedule": data.get("schedule"),
                    "status": None if active is None else "active" if active else "stopped",
                    "since": data.get("recorded_at"),
                    "notes": data.get("notes"),
                }
            elif section == "conditions":
                entry = {
                    "name": data.get("name"),
                    "status": data.get("status"),
                    "since": _text(data.get("diagnosed_at")),
                    "notes": data.get("notes"),
                }
            else:
                entry = {
                    "text": data.get("content"),
                    "predicate": data.get("predicate"),
                    **(
                        {"since": data.get("valid_at") or data.get("created_at")}
                        if section == "allergies"
                        else {"entity_id": data.get("entity_id")}
                    ),
                    "fields": found["json"].get(row["source_id"], {}),
                }
            entry["source"] = source
            entries.append((section, entry))

    def build(kept: list, _: bool) -> dict[str, Any]:
        grouped: dict[str, Any] = {section: [] for section in SECTIONS}
        for section, entry in kept:
            grouped[section].append(entry)
        return grouped

    # One ordered list (medications, conditions, allergies, providers) so a cut drops the tail.
    result = fit(entries, build)
    if cut:
        result["truncated"] = True
    return result


async def health_records(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """One page of one published health table, newest first (REQ-public-mcp-surface-014)."""
    return await read_records(conn, context, arguments, tool="health_records", domain="health")
