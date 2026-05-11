"""Health butler endpoints.

Provides endpoints for measurements, medications, doses, conditions,
symptoms, meals, and research. All data is queried directly from the
health butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("health_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["health_api_models"] = _models
    _spec.loader.exec_module(_models)

    Condition = _models.Condition
    Dose = _models.Dose
    LatestMeasurementEntry = _models.LatestMeasurementEntry
    LatestMeasurementsResponse = _models.LatestMeasurementsResponse
    Meal = _models.Meal
    Measurement = _models.Measurement
    MeasurementSource = _models.MeasurementSource
    MeasurementSourcesResponse = _models.MeasurementSourcesResponse
    Medication = _models.Medication
    Research = _models.Research
    SleepSessionResponse = _models.SleepSessionResponse
    SleepStage = _models.SleepStage
    Symptom = _models.Symptom

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])

BUTLER_DB = "health"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the health butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Health butler database is not available",
        )


# ---------------------------------------------------------------------------
# GET /measurements — list measurements
#
# Storage: reads from the `facts` table using predicates of the form
# `measurement_{type}` (e.g. `measurement_weight`).  This matches the write
# path used by the `measurement_log` MCP tool, which stores measurements as
# facts with `scope='health'` and `metadata.value` holding the typed value.
#
# The legacy `health.measurements` table from migration `health_001` is no
# longer written to.  Migration `health_002` drops that table.  Any data
# that existed only in `measurements` and was never written via the tool
# will not appear here; in practice the tool is the only write path.
# ---------------------------------------------------------------------------


@router.get("/measurements", response_model=PaginatedResponse[Measurement])
async def list_measurements(
    type: str | None = Query(None, description="Filter by measurement type"),
    since: str | None = Query(None, description="Filter from this timestamp (inclusive)"),
    until: str | None = Query(None, description="Filter up to this timestamp (inclusive)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Measurement]:
    """List measurements with optional type and date range filters.

    Reads from the ``facts`` table (predicate = ``measurement_{type}``,
    scope = ``health``), which is the same surface written by the
    ``measurement_log`` MCP tool.
    """
    pool = _pool(db)

    # Base predicate filter — either a specific type or the set of all known types.
    _VALID_TYPES = ("weight", "blood_pressure", "heart_rate", "blood_sugar", "temperature")
    if type is not None:
        predicate_cond = "predicate = $1"
        args: list[object] = [f"measurement_{type}"]
        idx = 2
    else:
        predicate_cond = "predicate = ANY($1)"
        args = [[f"measurement_{t}" for t in _VALID_TYPES]]
        idx = 2

    base_where = f"{predicate_cond} AND scope = 'health' AND validity = 'active'"
    extra: list[str] = []

    if since is not None:
        extra.append(f"valid_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        extra.append(f"valid_at <= ${idx}")
        args.append(until)
        idx += 1

    where = base_where + ("".join(f" AND {c}" for c in extra))

    total = await pool.fetchval(f"SELECT count(*) FROM facts WHERE {where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, predicate, valid_at, created_at, metadata"
        f" FROM facts WHERE {where}"
        f" ORDER BY valid_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = []
    for r in rows:
        meta = _as_json_object(r["metadata"])
        mtype = r["predicate"].removeprefix("measurement_")
        raw_value = meta.get("value")
        # Normalise scalar values to a dict for the Measurement model (value: dict).
        if not isinstance(raw_value, dict):
            raw_value = {"value": raw_value}
        data.append(
            Measurement(
                id=str(r["id"]),
                type=mtype,
                value=raw_value,
                measured_at=str(r["valid_at"]),
                notes=meta.get("notes"),
                created_at=str(r["created_at"]),
            )
        )

    return PaginatedResponse[Measurement](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /medications — list medications
# ---------------------------------------------------------------------------


@router.get("/medications", response_model=PaginatedResponse[Medication])
async def list_medications(
    active: bool | None = Query(None, description="Filter by active status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Medication]:
    """List medications with optional active status filter."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if active is not None:
        conditions.append(f"active = ${idx}")
        args.append(active)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM medications{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, name, dosage, frequency, schedule, active, notes, created_at, updated_at"
        f" FROM medications{where}"
        f" ORDER BY name"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        Medication(
            id=str(r["id"]),
            name=r["name"],
            dosage=r["dosage"],
            frequency=r["frequency"],
            schedule=list(r["schedule"]) if r["schedule"] else [],
            active=r["active"],
            notes=r["notes"],
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[Medication](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /medications/{medication_id}/doses — dose log for a medication
# ---------------------------------------------------------------------------


@router.get("/medications/{medication_id}/doses", response_model=list[Dose])
async def list_medication_doses(
    medication_id: str,
    since: str | None = Query(None, description="Filter from this timestamp (inclusive)"),
    until: str | None = Query(None, description="Filter up to this timestamp (inclusive)"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[Dose]:
    """List dose log entries for a specific medication."""
    pool = _pool(db)

    conditions: list[str] = ["medication_id = $1"]
    args: list[object] = [medication_id]
    idx = 2

    if since is not None:
        conditions.append(f"taken_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"taken_at <= ${idx}")
        args.append(until)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    rows = await pool.fetch(
        f"SELECT id, medication_id, taken_at, skipped, notes, created_at"
        f" FROM medication_doses{where}"
        f" ORDER BY taken_at DESC",
        *args,
    )

    return [
        Dose(
            id=str(r["id"]),
            medication_id=str(r["medication_id"]),
            taken_at=str(r["taken_at"]),
            skipped=r["skipped"],
            notes=r["notes"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /conditions — list conditions
# ---------------------------------------------------------------------------


@router.get("/conditions", response_model=PaginatedResponse[Condition])
async def list_conditions(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Condition]:
    """List health conditions."""
    pool = _pool(db)

    total = await pool.fetchval("SELECT count(*) FROM conditions") or 0

    rows = await pool.fetch(
        "SELECT id, name, status, diagnosed_at, notes, created_at, updated_at"
        " FROM conditions"
        " ORDER BY created_at DESC"
        " OFFSET $1 LIMIT $2",
        offset,
        limit,
    )

    data = [
        Condition(
            id=str(r["id"]),
            name=r["name"],
            status=r["status"],
            diagnosed_at=str(r["diagnosed_at"]) if r["diagnosed_at"] else None,
            notes=r["notes"],
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[Condition](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /symptoms — list symptoms
# ---------------------------------------------------------------------------


@router.get("/symptoms", response_model=PaginatedResponse[Symptom])
async def list_symptoms(
    name: str | None = Query(None, description="Filter by symptom name"),
    since: str | None = Query(None, description="Filter from this timestamp (inclusive)"),
    until: str | None = Query(None, description="Filter up to this timestamp (inclusive)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Symptom]:
    """List symptoms with optional name and date range filters."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if name is not None:
        conditions.append(f"name ILIKE '%' || ${idx} || '%'")
        args.append(name)
        idx += 1

    if since is not None:
        conditions.append(f"occurred_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"occurred_at <= ${idx}")
        args.append(until)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM symptoms{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, name, severity, condition_id, occurred_at, notes, created_at"
        f" FROM symptoms{where}"
        f" ORDER BY occurred_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        Symptom(
            id=str(r["id"]),
            name=r["name"],
            severity=r["severity"],
            condition_id=str(r["condition_id"]) if r["condition_id"] else None,
            occurred_at=str(r["occurred_at"]),
            notes=r["notes"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[Symptom](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /meals — list meals (backed by temporal facts)
# ---------------------------------------------------------------------------

_MEAL_PREDICATES = ["meal_breakfast", "meal_lunch", "meal_dinner", "meal_snack"]


def _as_json_object(value: object) -> dict:
    """Normalize asyncpg JSON/JSONB outputs into a dict."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _meal_nutrition_from_metadata(metadata: dict) -> dict | None:
    """Build backward-compatible Meal.nutrition from fact metadata fields."""
    estimated_calories = metadata.get("estimated_calories")
    macros = _as_json_object(metadata.get("macros"))
    has_nutrition = estimated_calories is not None or any(
        macros.get(k) is not None for k in ("protein_g", "carbs_g", "fat_g")
    )
    if not has_nutrition:
        return None
    return {
        "calories": estimated_calories,
        "protein_g": macros.get("protein_g"),
        "carbs_g": macros.get("carbs_g"),
        "fat_g": macros.get("fat_g"),
    }


@router.get("/meals", response_model=PaginatedResponse[Meal])
async def list_meals(
    type: str | None = Query(None, description="Filter by meal type"),
    since: str | None = Query(None, description="Filter from this timestamp (inclusive)"),
    until: str | None = Query(None, description="Filter up to this timestamp (inclusive)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Meal]:
    """List meals with optional type and date range filters."""
    pool = _pool(db)

    predicates = [f"meal_{type}"] if type is not None else _MEAL_PREDICATES
    args: list[object] = [predicates]  # $1 = predicate array
    idx = 2

    extra: list[str] = []
    if since is not None:
        extra.append(f"valid_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        extra.append(f"valid_at <= ${idx}")
        args.append(until)
        idx += 1

    where = "predicate = ANY($1) AND scope = 'health' AND validity = 'active'" + (
        "".join(f" AND {c}" for c in extra)
    )

    total = await pool.fetchval(f"SELECT count(*) FROM facts WHERE {where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, predicate, content, valid_at, created_at, metadata"
        f" FROM facts WHERE {where}"
        f" ORDER BY valid_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = []
    for r in rows:
        meta = _as_json_object(r["metadata"])
        meal_type = r["predicate"].removeprefix("meal_")
        data.append(
            Meal(
                id=str(r["id"]),
                type=meal_type,
                description=r["content"],
                nutrition=_meal_nutrition_from_metadata(meta),
                eaten_at=str(r["valid_at"]),
                notes=meta.get("notes"),
                created_at=str(r["created_at"]),
            )
        )

    return PaginatedResponse[Meal](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /research — list/search research
# ---------------------------------------------------------------------------


@router.get("/research", response_model=PaginatedResponse[Research])
async def list_research(
    q: str | None = Query(None, description="Full-text search in title and content"),
    tag: str | None = Query(None, description="Filter by tag"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Research]:
    """List or search research entries."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if q is not None:
        conditions.append(
            f"(title ILIKE '%' || ${idx} || '%' OR content ILIKE '%' || ${idx} || '%')"
        )
        args.append(q)
        idx += 1

    if tag is not None:
        conditions.append(f"tags @> ${idx}::jsonb")
        args.append([tag])
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM research{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, title, content, tags, source_url, condition_id, created_at, updated_at"
        f" FROM research{where}"
        f" ORDER BY created_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        Research(
            id=str(r["id"]),
            title=r["title"],
            content=r["content"],
            tags=list(r["tags"]) if r["tags"] else [],
            source_url=r["source_url"],
            condition_id=str(r["condition_id"]) if r["condition_id"] else None,
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[Research](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /measurements/latest — latest row per requested type
# ---------------------------------------------------------------------------


@router.get("/measurements/latest", response_model=LatestMeasurementsResponse)
async def get_measurements_latest(
    types: str = Query(..., description="Comma-separated list of measurement types"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> LatestMeasurementsResponse:
    """Return the latest measurement row for each requested type.

    Types are provided as a comma-separated query string, e.g.
    ``?types=weight,heart_rate``.  Types with no data map to ``null``.

    SQL uses ``DISTINCT ON (predicate)`` to retrieve one row per type in a
    single round-trip against the ``facts`` table.  The pool is
    butler-scoped — no butler_name filter is applied.
    """
    pool = _pool(db)

    type_list = [t.strip() for t in types.split(",") if t.strip()]
    if not type_list:
        return LatestMeasurementsResponse(measurements={})

    predicate_list = [f"measurement_{t}" for t in type_list]

    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (predicate) predicate, valid_at, metadata
        FROM facts
        WHERE predicate = ANY($1::text[])
          AND scope = 'health'
          AND validity = 'active'
          AND valid_at IS NOT NULL
        ORDER BY predicate, valid_at DESC NULLS LAST
        """,
        predicate_list,
    )

    result: dict[str, LatestMeasurementEntry | None] = {t: None for t in type_list}
    for r in rows:
        mtype = r["predicate"].removeprefix("measurement_")
        meta = _as_json_object(r["metadata"])
        raw_value = meta.get("value")
        result[mtype] = LatestMeasurementEntry(
            measured_at=str(r["valid_at"]),
            value=raw_value,
            unit=meta.get("unit"),
            metadata={k: v for k, v in meta.items() if k not in ("value", "unit")},
        )

    return LatestMeasurementsResponse(measurements=result)


# ---------------------------------------------------------------------------
# GET /measurements/sleep/latest — most recent sleep session
# ---------------------------------------------------------------------------

# Stage-name normalisation: Google Health returns camelCase or underscore keys.
_SLEEP_STAGE_ALIASES: dict[str, str] = {
    "deep": "deep",
    "deep_sleep": "deep",
    "deepSleep": "deep",
    "light": "light",
    "light_sleep": "light",
    "lightSleep": "light",
    "rem": "rem",
    "rem_sleep": "rem",
    "remSleep": "rem",
    "awake": "awake",
    "wake": "awake",
}


def _parse_sleep_stages(stages: dict | None) -> list[SleepStage]:
    """Convert a raw stages dict from fact metadata into SleepStage entries."""
    if not stages or not isinstance(stages, dict):
        return []
    result: list[SleepStage] = []
    for raw_kind, raw_minutes in stages.items():
        kind = _SLEEP_STAGE_ALIASES.get(raw_kind)
        if kind is None:
            continue
        try:
            minutes = int(raw_minutes)
        except (TypeError, ValueError):
            continue
        result.append(SleepStage(kind=kind, minutes=minutes))
    return result


@router.get("/measurements/sleep/latest", response_model=SleepSessionResponse | None)
async def get_sleep_latest(
    db: DatabaseManager = Depends(_get_db_manager),
) -> SleepSessionResponse | None:
    """Return the most recent sleep session.

    Sleep data is stored in the ``facts`` table by the Google Health
    connector using predicate ``sleep_session``.  ``total_duration_minutes``
    is derived from ``metadata.duration_ms``.  Returns HTTP 200 with a JSON
    ``null`` body when no sleep session exists yet.

    The pool is butler-scoped — no butler_name filter is applied.
    """
    pool = _pool(db)

    row = await pool.fetchrow(
        """
        SELECT id, valid_at, metadata
        FROM facts
        WHERE predicate = 'sleep_session'
          AND validity = 'active'
          AND scope = 'health'
        ORDER BY valid_at DESC
        LIMIT 1
        """
    )

    if row is None:
        return None

    meta = _as_json_object(row["metadata"])

    duration_ms = int(meta.get("duration_ms") or 0)
    total_duration_minutes = duration_ms // 60_000 if duration_ms > 0 else 0

    stages = _parse_sleep_stages(meta.get("stages"))

    session_start = str(row["valid_at"])
    end_time = meta.get("end_time")
    session_end = str(end_time) if end_time else None

    return SleepSessionResponse(
        session_start=session_start,
        session_end=session_end,
        total_duration_minutes=total_duration_minutes,
        stages=stages,
    )


# ---------------------------------------------------------------------------
# GET /measurements/sources — data sources observed in facts metadata
# ---------------------------------------------------------------------------


@router.get("/measurements/sources", response_model=MeasurementSourcesResponse)
async def get_measurements_sources(
    db: DatabaseManager = Depends(_get_db_manager),
) -> MeasurementSourcesResponse:
    """Return all data sources observed across measurements.

    Reads from the ``facts`` table — facts with predicates of the form
    ``measurement_*`` store their source in ``metadata->>'source'``.  Rows
    with a missing or empty source are excluded.

    The pool is butler-scoped — no butler_name filter is applied.
    """
    pool = _pool(db)

    rows = await pool.fetch(
        """
        SELECT
            metadata->>'source'  AS name,
            MAX(valid_at)        AS last_sample_at,
            COUNT(*)             AS sample_count
        FROM facts
        WHERE predicate LIKE 'measurement~_%' ESCAPE '~'
          AND scope = 'health'
          AND validity = 'active'
          AND metadata->>'source' IS NOT NULL
          AND metadata->>'source' <> ''
        GROUP BY metadata->>'source'
        ORDER BY last_sample_at DESC
        """
    )

    sources = [
        MeasurementSource(
            name=r["name"],
            last_sample_at=str(r["last_sample_at"]),
            sample_count=int(r["sample_count"]),
        )
        for r in rows
    ]

    return MeasurementSourcesResponse(sources=sources)
