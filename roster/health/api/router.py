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
    TrendBucket = _models.TrendBucket
    TrendResponse = _models.TrendResponse

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

    # Base predicate filter — either a specific type or all measurement facts.
    # When no type is specified, use a prefix LIKE filter so that wellness-ingest
    # measurements (measurement_spo2, measurement_steps, etc.) are included
    # alongside the core tool types.  This matches the approach used by
    # GET /measurements/sources.
    if type is not None:
        predicate_cond = "predicate = $1"
        args: list[object] = [f"measurement_{type}"]
        idx = 2
    else:
        predicate_cond = "predicate LIKE 'measurement~_%' ESCAPE '~'"
        args = []
        idx = 1

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
                measured_at=r["valid_at"].isoformat(),
                notes=meta.get("notes"),
                created_at=r["created_at"].isoformat(),
            )
        )

    return PaginatedResponse[Measurement](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /medications — list medications
#
# Storage: reads from the `facts` table using predicate `medication`
# (scope = 'health', validity = 'active').  This matches the write path of the
# `medication_add` MCP tool, which stores medications as property facts with
# name/dosage/frequency/schedule/active/notes carried in `metadata`.  The legacy
# `health.medications` relational table is orphaned (no longer written).
# ---------------------------------------------------------------------------


@router.get("/medications", response_model=PaginatedResponse[Medication])
async def list_medications(
    active: bool | None = Query(None, description="Filter by active status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Medication]:
    """List medications with optional active status filter.

    Reads from the ``facts`` table (predicate = ``medication``,
    scope = ``health``), the same surface written by the ``medication_add``
    MCP tool.
    """
    pool = _pool(db)

    conditions: list[str] = [
        "predicate = 'medication'",
        "validity = 'active'",
        "scope = 'health'",
    ]
    args: list[object] = []
    idx = 1

    if active is not None:
        conditions.append(f"(metadata->>'active')::boolean = ${idx}")
        args.append(active)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    total = await pool.fetchval(f"SELECT count(*) FROM facts{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, content, created_at, metadata"
        f" FROM facts{where}"
        f" ORDER BY metadata->>'name'"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = []
    for r in rows:
        meta = _as_json_object(r["metadata"])
        data.append(
            Medication(
                id=str(r["id"]),
                name=meta.get("name", ""),
                dosage=meta.get("dosage", ""),
                frequency=meta.get("frequency", ""),
                schedule=list(meta.get("schedule") or []),
                active=bool(meta.get("active", True)),
                notes=meta.get("notes"),
                created_at=r["created_at"].isoformat(),
                updated_at=r["created_at"].isoformat(),
            )
        )

    return PaginatedResponse[Medication](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /medications/{medication_id}/doses — dose log for a medication
#
# Storage: reads from the `facts` table using predicate `took_dose`
# (scope = 'health', validity = 'active').  This matches the write path of the
# `medication_log_dose` MCP tool, which stores each dose as a temporal fact with
# `valid_at` = taken_at and metadata carrying `medication_id`/`skipped`/`notes`.
# The legacy `health.medication_doses` relational table is orphaned.
# ---------------------------------------------------------------------------


@router.get("/medications/{medication_id}/doses", response_model=list[Dose])
async def list_medication_doses(
    medication_id: str,
    since: str | None = Query(None, description="Filter from this timestamp (inclusive)"),
    until: str | None = Query(None, description="Filter up to this timestamp (inclusive)"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[Dose]:
    """List dose log entries for a specific medication.

    Reads from the ``facts`` table (predicate = ``took_dose``,
    scope = ``health``), the same surface written by the ``medication_log_dose``
    MCP tool.  Doses are scoped to the medication via
    ``metadata->>'medication_id'``.
    """
    pool = _pool(db)

    conditions: list[str] = [
        "predicate = 'took_dose'",
        "validity = 'active'",
        "scope = 'health'",
        "metadata->>'medication_id' = $1",
    ]
    args: list[object] = [medication_id]
    idx = 2

    if since is not None:
        conditions.append(f"valid_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"valid_at <= ${idx}")
        args.append(until)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    rows = await pool.fetch(
        f"SELECT id, valid_at, created_at, metadata FROM facts{where} ORDER BY valid_at DESC",
        *args,
    )

    data = []
    for r in rows:
        meta = _as_json_object(r["metadata"])
        data.append(
            Dose(
                id=str(r["id"]),
                medication_id=str(meta.get("medication_id", medication_id)),
                taken_at=r["valid_at"].isoformat(),
                skipped=bool(meta.get("skipped", False)),
                notes=meta.get("notes"),
                created_at=r["created_at"].isoformat(),
            )
        )
    return data


# ---------------------------------------------------------------------------
# GET /conditions — list conditions
#
# Storage: reads from the `facts` table using predicate `condition`
# (scope = 'health', validity = 'active').  This matches the write path of the
# `condition_add` / `condition_update` MCP tools, which store conditions as
# property facts with name/status/diagnosed_at/notes carried in `metadata`.
# The legacy `health.conditions` relational table is orphaned.
# ---------------------------------------------------------------------------


@router.get("/conditions", response_model=PaginatedResponse[Condition])
async def list_conditions(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Condition]:
    """List health conditions.

    Reads from the ``facts`` table (predicate = ``condition``,
    scope = ``health``), the same surface written by the ``condition_add``
    MCP tool.
    """
    pool = _pool(db)

    where = " WHERE predicate = 'condition' AND validity = 'active' AND scope = 'health'"

    total = await pool.fetchval(f"SELECT count(*) FROM facts{where}") or 0

    rows = await pool.fetch(
        f"SELECT id, content, created_at, metadata"
        f" FROM facts{where}"
        f" ORDER BY created_at DESC"
        f" OFFSET $1 LIMIT $2",
        offset,
        limit,
    )

    data = []
    for r in rows:
        meta = _as_json_object(r["metadata"])
        data.append(
            Condition(
                id=str(r["id"]),
                name=meta.get("name", r["content"] or ""),
                status=meta.get("status", "active"),
                diagnosed_at=meta.get("diagnosed_at"),
                notes=meta.get("notes"),
                created_at=r["created_at"].isoformat(),
                updated_at=r["created_at"].isoformat(),
            )
        )

    return PaginatedResponse[Condition](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /symptoms — list symptoms
#
# Storage: reads from the `facts` table using predicate `symptom`
# (scope = 'health', validity = 'active').  This matches the write path of the
# `symptom_log` MCP tool, which stores each symptom as a temporal fact with
# `content` = name, `valid_at` = occurred_at, and severity/condition_id/notes
# carried in `metadata`.  The legacy `health.symptoms` relational table is
# orphaned.
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
    """List symptoms with optional name and date range filters.

    Reads from the ``facts`` table (predicate = ``symptom``,
    scope = ``health``), the same surface written by the ``symptom_log``
    MCP tool.  The symptom name lives in ``content``; ``valid_at`` is the
    occurrence timestamp.
    """
    pool = _pool(db)

    conditions: list[str] = [
        "predicate = 'symptom'",
        "validity = 'active'",
        "scope = 'health'",
    ]
    args: list[object] = []
    idx = 1

    if name is not None:
        conditions.append(f"content ILIKE '%' || ${idx} || '%'")
        args.append(name)
        idx += 1

    if since is not None:
        conditions.append(f"valid_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"valid_at <= ${idx}")
        args.append(until)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    total = await pool.fetchval(f"SELECT count(*) FROM facts{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, content, valid_at, created_at, metadata"
        f" FROM facts{where}"
        f" ORDER BY valid_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = []
    for r in rows:
        meta = _as_json_object(r["metadata"])
        cond_id = meta.get("condition_id")
        data.append(
            Symptom(
                id=str(r["id"]),
                name=r["content"] or "",
                severity=int(meta.get("severity")) if meta.get("severity") is not None else 0,
                condition_id=str(cond_id) if cond_id else None,
                occurred_at=r["valid_at"].isoformat(),
                notes=meta.get("notes"),
                created_at=r["created_at"].isoformat(),
            )
        )

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
                eaten_at=r["valid_at"].isoformat(),
                notes=meta.get("notes"),
                created_at=r["created_at"].isoformat(),
            )
        )

    return PaginatedResponse[Meal](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /research — list/search research
#
# Storage: reads from the `facts` table using predicate `research`
# (scope = 'health', validity = 'active').  This matches the write path of the
# `research_save` MCP tool, which stores research notes as property facts with
# `content` = body and title/tags/source_url/condition_id carried in
# `metadata`.  The legacy `health.research` relational table is orphaned.
# ---------------------------------------------------------------------------


@router.get("/research", response_model=PaginatedResponse[Research])
async def list_research(
    q: str | None = Query(None, description="Full-text search in title and content"),
    tag: str | None = Query(None, description="Filter by tag"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Research]:
    """List or search research entries.

    Reads from the ``facts`` table (predicate = ``research``,
    scope = ``health``), the same surface written by the ``research_save``
    MCP tool.  The title lives in ``metadata->>'title'`` and the note body in
    ``content``.
    """
    pool = _pool(db)

    conditions: list[str] = [
        "predicate = 'research'",
        "validity = 'active'",
        "scope = 'health'",
    ]
    args: list[object] = []
    idx = 1

    if q is not None:
        conditions.append(
            f"(metadata->>'title' ILIKE '%' || ${idx} || '%' OR content ILIKE '%' || ${idx} || '%')"
        )
        args.append(q)
        idx += 1

    if tag is not None:
        conditions.append(f"metadata->'tags' @> ${idx}::jsonb")
        args.append(json.dumps([tag]))
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    total = await pool.fetchval(f"SELECT count(*) FROM facts{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, content, created_at, metadata"
        f" FROM facts{where}"
        f" ORDER BY created_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = []
    for r in rows:
        meta = _as_json_object(r["metadata"])
        cond_id = meta.get("condition_id")
        data.append(
            Research(
                id=str(r["id"]),
                title=meta.get("title", ""),
                content=r["content"] or "",
                tags=list(meta.get("tags") or []),
                source_url=meta.get("source_url"),
                condition_id=str(cond_id) if cond_id else None,
                created_at=r["created_at"].isoformat(),
                updated_at=r["created_at"].isoformat(),
            )
        )

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
            measured_at=r["valid_at"].isoformat(),
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

    session_start = row["valid_at"].isoformat()
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
            last_sample_at=r["last_sample_at"].isoformat(),
            sample_count=int(r["sample_count"]),
        )
        for r in rows
    ]

    return MeasurementSourcesResponse(sources=sources)


# ---------------------------------------------------------------------------
# GET /measurements/trend — hourly/daily aggregation for dashboard sparklines
#
# Aggregates facts rows for a single measurement type into time buckets.
# Designed for high-frequency biosensors (e.g. CGM at 5-min intervals) where
# the 50-row default on /measurements is insufficient for trend visualisation.
#
# Pool is butler-scoped — no butler_name filter is applied.
# ---------------------------------------------------------------------------

_TREND_WINDOW_DAYS_ALLOWED = {1, 7, 14, 30, 90}


@router.get("/measurements/trend", response_model=TrendResponse)
async def get_measurements_trend(
    type: str = Query(..., description="Measurement type (e.g. 'glucose', 'weight')"),
    window_days: int = Query(14, description="Lookback window in days (1, 7, 14, 30, or 90)"),
    bucket: str = Query("daily", description="Bucket granularity: 'hourly' or 'daily'"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> TrendResponse:
    """Return aggregated trend buckets for a single measurement type.

    Reads from the ``facts`` table using ``predicate = 'measurement_{type}'``.
    Results are grouped by hour or day within the requested window.  Returns
    an empty ``buckets`` list when no data exists.

    Allowed ``window_days`` values: 1, 7, 14, 30, 90.
    Allowed ``bucket`` values: ``'hourly'``, ``'daily'``.

    The pool is butler-scoped — no butler_name filter is applied.
    """
    if window_days not in _TREND_WINDOW_DAYS_ALLOWED:
        raise HTTPException(
            status_code=422,
            detail=f"window_days must be one of {sorted(_TREND_WINDOW_DAYS_ALLOWED)}",
        )
    if bucket not in ("hourly", "daily"):
        raise HTTPException(
            status_code=422,
            detail="bucket must be 'hourly' or 'daily'",
        )

    pool = _pool(db)

    trunc_unit = "hour" if bucket == "hourly" else "day"
    predicate = f"measurement_{type}"

    rows = await pool.fetch(
        f"""
        SELECT
          date_trunc('{trunc_unit}', valid_at AT TIME ZONE 'UTC') AS bucket_start,
          AVG((metadata->>'value')::float8)      AS value_mean,
          MIN((metadata->>'value')::float8)      AS value_min,
          MAX((metadata->>'value')::float8)      AS value_max,
          COUNT(*)                               AS sample_count
        FROM facts
        WHERE predicate = $1
          AND scope = 'health'
          AND validity = 'active'
          AND valid_at IS NOT NULL
          AND valid_at >= NOW() - ($2 * INTERVAL '1 day')
          AND metadata ? 'value'
          AND jsonb_typeof(metadata->'value') IN ('number', 'string')
          AND (metadata->>'value') ~ '^-?[0-9]+(\\.[0-9]+)?$'
        GROUP BY bucket_start
        ORDER BY bucket_start ASC
        """,
        predicate,
        window_days,
    )

    buckets = [
        TrendBucket(
            bucket_start=r["bucket_start"],
            value_mean=float(r["value_mean"]),
            value_min=float(r["value_min"]),
            value_max=float(r["value_max"]),
            sample_count=int(r["sample_count"]),
        )
        for r in rows
    ]

    return TrendResponse(
        type=type,
        window_days=window_days,
        bucket=bucket,
        buckets=buckets,
    )
