"""Health butler endpoints.

Provides endpoints for measurements, medications, doses, conditions,
symptoms, meals, and research. All data is queried directly from the
health butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from butlers.api.briefing.cache import BriefingCache
from butlers.api.briefing.classify import time_of_day
from butlers.api.briefing.lint import first_violation, voice_lint_passes
from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.general_settings import load_general_settings
from butlers.core.model_routing import Complexity

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("health_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["health_api_models"] = _models
    _spec.loader.exec_module(_models)

    Briefing = _models.Briefing
    Condition = _models.Condition
    ConditionCreateRequest = _models.ConditionCreateRequest
    ConditionUpdateRequest = _models.ConditionUpdateRequest
    Dose = _models.Dose
    DoseLogRequest = _models.DoseLogRequest
    LatestMeasurementEntry = _models.LatestMeasurementEntry
    LatestMeasurementsResponse = _models.LatestMeasurementsResponse
    Meal = _models.Meal
    MealCreateRequest = _models.MealCreateRequest
    MealUpdateRequest = _models.MealUpdateRequest
    Measurement = _models.Measurement
    MeasurementCreateRequest = _models.MeasurementCreateRequest
    MeasurementUpdateRequest = _models.MeasurementUpdateRequest
    MeasurementSource = _models.MeasurementSource
    MeasurementSourcesResponse = _models.MeasurementSourcesResponse
    Medication = _models.Medication
    MedicationAdherenceResponse = _models.MedicationAdherenceResponse
    MedicationCreateRequest = _models.MedicationCreateRequest
    MedicationUpdateRequest = _models.MedicationUpdateRequest
    NutritionDailyAverage = _models.NutritionDailyAverage
    NutritionSummaryResponse = _models.NutritionSummaryResponse
    Research = _models.Research
    ResearchCreateRequest = _models.ResearchCreateRequest
    ResearchUpdateRequest = _models.ResearchUpdateRequest
    SleepSessionResponse = _models.SleepSessionResponse
    SleepStage = _models.SleepStage
    Symptom = _models.Symptom
    SymptomCreateRequest = _models.SymptomCreateRequest
    SymptomUpdateRequest = _models.SymptomUpdateRequest
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
# POST/PUT/DELETE /measurements — direct dashboard CRUD
#
# These mutations persist through the SAME fact-store path the Health butler's
# own MCP tools use:
#   - POST   -> measurement_log    (predicate 'measurement_{type}', TEMPORAL fact)
#   - PUT    -> measurement_update (in-place UPDATE of the existing fact)
#   - DELETE -> measurement_delete (forget_memory -> validity = 'retracted')
# so a dashboard-logged reading is indistinguishable from a butler-logged one
# and is read back by GET /measurements above.  Measurements are TEMPORAL facts:
# ``measured_at`` becomes the fact's ``valid_at`` and multiple readings coexist
# by design — there is NO supersession (unlike conditions/medications), so the
# update path edits the existing fact row in place rather than re-storing.  The
# measurement type is encoded in the predicate, so changing it rewrites the
# ``measurement_{type}`` predicate.  No new predicates, tables, or DDL.
# ---------------------------------------------------------------------------


def _normalize_measurement_value(value: object) -> object:
    """Unwrap a single-key ``{"value": x}`` dict to its scalar.

    The Measurement response model carries ``value`` as a dict, so the dashboard
    sends scalar readings (e.g. weight) wrapped as ``{"value": 165}``.  The
    fact-store, however, stores scalars natively (``measurement_log`` accepts
    ``Any``).  Unwrapping the single-key form keeps round-trips consistent with
    butler-logged readings; compound values (blood pressure, etc.) pass through
    untouched.
    """
    if isinstance(value, dict) and set(value.keys()) == {"value"}:
        return value["value"]
    return value


def _measurement_response(result: dict) -> Measurement:
    """Build the Measurement response model from a write-tool result dict."""
    raw_value = result.get("value")
    if not isinstance(raw_value, dict):
        raw_value = {"value": raw_value}
    return Measurement(
        id=str(result["id"]),
        type=result.get("type", ""),
        value=raw_value,
        measured_at=_isoformat(result.get("measured_at")),
        notes=result.get("notes"),
        created_at=_isoformat(result.get("created_at")),
    )


@router.post("/measurements", response_model=Measurement, status_code=status.HTTP_201_CREATED)
async def create_measurement(
    body: MeasurementCreateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Measurement:
    """Log a measurement via the butler's ``measurement_log`` fact-store path.

    Writes a temporal fact (predicate = ``measurement_{type}``, scope =
    ``health``, ``valid_at`` = measured_at) — the same surface the
    ``measurement_log`` MCP tool writes — so the new reading appears in
    GET /measurements immediately.  Returns 404 if ``type`` is unrecognized.
    """
    from butlers.tools.health import measurement_log

    pool = _pool(db)
    try:
        result = await measurement_log(
            pool,
            type=body.type,
            value=_normalize_measurement_value(body.value),
            notes=body.notes,
            measured_at=body.measured_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _measurement_response(result)


@router.put("/measurements/{measurement_id}", response_model=Measurement)
async def update_measurement(
    measurement_id: str,
    body: MeasurementUpdateRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> Measurement:
    """Update a measurement via the in-place ``measurement_update`` fact path.

    Only the supplied (non-null) fields are applied to the existing measurement
    fact.  Because measurements are temporal facts, the edit updates the
    existing row in place (rather than superseding it); changing ``type``
    rewrites the ``measurement_{type}`` predicate.  Returns 404 if the
    measurement does not exist and 422 if no updatable fields were provided.
    """
    from butlers.tools.health import measurement_update

    pool = _pool(db)
    updates = body.model_dump(exclude_none=True)
    if "value" in updates:
        updates["value"] = _normalize_measurement_value(updates["value"])
    if not updates:
        raise HTTPException(
            status_code=422,
            detail="No updatable fields provided",
        )
    try:
        result = await measurement_update(pool, measurement_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _measurement_response(result)


@router.delete("/measurements/{measurement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_measurement(
    measurement_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete a measurement via ``measurement_delete`` (validity = retracted).

    The fact is retained for audit but excluded from all active read surfaces.
    Returns 204 on success and 404 if no active measurement with this id exists.
    """
    from butlers.tools.health import measurement_delete

    pool = _pool(db)
    try:
        await measurement_delete(pool, measurement_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


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
# POST /medications/{medication_id}/doses — log (or skip) a dose
#
# Persists through the SAME fact-store path the Health butler's MCP tool uses:
#   POST -> medication_log_dose (predicate 'took_dose', TEMPORAL fact,
#           valid_at = taken_at, metadata carries medication_id/skipped/notes)
# so a dashboard-logged dose is indistinguishable from a butler-logged one and
# is read back by GET /medications/{medication_id}/doses above.  No new
# predicates, tables, or DDL are introduced.
# ---------------------------------------------------------------------------


# --- Per-owner 5-minute TTL cache (reuses the shared BriefingCache) ---------
# Defined here (above the dose-logging route) so the route can reference
# get_health_briefing_cache as a FastAPI dependency at definition time; the
# GET /briefing route below shares the same singleton.
_health_briefing_cache: BriefingCache = BriefingCache()


def get_health_briefing_cache() -> BriefingCache:
    """Return the module-level health-briefing cache singleton (FastAPI dep)."""
    return _health_briefing_cache


def replace_health_briefing_cache(cache: BriefingCache) -> None:
    """Replace the module-level cache (used in tests to inject a zero-TTL cache)."""
    global _health_briefing_cache
    _health_briefing_cache = cache


def _dose_response(result: dict, medication_id: str) -> Dose:
    """Build the Dose response model from a write-tool result dict."""
    return Dose(
        id=str(result["id"]),
        medication_id=str(result.get("medication_id", medication_id)),
        taken_at=_isoformat(result.get("taken_at")),
        skipped=bool(result.get("skipped", False)),
        notes=result.get("notes"),
        created_at=_isoformat(result.get("created_at")),
    )


@router.post(
    "/medications/{medication_id}/doses",
    response_model=Dose,
    status_code=status.HTTP_201_CREATED,
)
async def log_medication_dose(
    medication_id: str,
    body: DoseLogRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
    cache: BriefingCache = Depends(get_health_briefing_cache),
) -> Dose:
    """Log a medication dose via the butler's ``medication_log_dose`` fact path.

    Writes a temporal fact (predicate = ``took_dose``, scope = ``health``,
    ``valid_at`` = taken_at) — the same surface the ``medication_log_dose`` MCP
    tool writes — so the dose appears in GET /medications/{id}/doses immediately.
    Set ``skipped=True`` to record a missed dose.  Returns 404 if the medication
    does not exist.

    After a successful write the per-owner health-briefing cache is invalidated
    so the next ``GET /api/health/briefing`` reflects the new dose rather than
    serving a pre-dose cached paragraph for up to the 5-minute TTL (spec:
    "Logging a dose ... MUST invalidate the per-owner briefing cache").  The
    deployment is single-owner, so ``invalidate_all`` clears the one owner entry
    without a switchboard-pool owner lookup on the write path.
    """
    from butlers.tools.health import medication_log_dose

    pool = _pool(db)
    try:
        result = await medication_log_dose(
            pool,
            medication_id,
            taken_at=body.taken_at,
            skipped=body.skipped,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    cache.invalidate_all()
    return _dose_response(result, medication_id)


# ---------------------------------------------------------------------------
# GET /medications/{medication_id}/adherence — dose adherence summary
#
# Aggregates the `took_dose` facts scoped to one medication (the same surface
# medication_log_dose writes) into total/taken/skipped/expected counts plus an
# adherence rate, via the `medication_history` tool.
#
# ``expected_doses`` is computed from the medication's prescribed frequency
# over the query window using ``frequency_to_doses_per_day`` from the shared
# helper module — the same denominator the insight-scan job uses (spec:
# "Shared denominator with insight job").  Read-only; no DDL.
# ---------------------------------------------------------------------------


_DEFAULT_ADHERENCE_WINDOW_DAYS = 30


@router.get(
    "/medications/{medication_id}/adherence",
    response_model=MedicationAdherenceResponse,
)
async def get_medication_adherence(
    medication_id: str,
    start: datetime | None = Query(None, description="Window start (inclusive)"),
    end: datetime | None = Query(None, description="Window end (inclusive)"),
    window_days: int | None = Query(
        None,
        description=(
            f"Lookback window in days when start/end are not supplied "
            f"(default {_DEFAULT_ADHERENCE_WINDOW_DAYS})"
        ),
        ge=1,
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> MedicationAdherenceResponse:
    """Return frequency-expected dose-adherence stats for a single medication.

    Aggregates ``took_dose`` facts (scope = ``health``) for this medication over
    an optional ``start``/``end`` window (or ``window_days`` lookback, defaulting
    to 30 days) via the ``medication_history`` tool.

    ``expected_doses`` is computed from the medication's prescribed frequency
    over the window using the shared ``frequency_to_doses_per_day`` helper —
    the same denominator the insight-scan job uses.  ``adherence_rate`` is the
    percentage of non-skipped doses out of ``expected_doses`` (``null`` when
    ``expected_doses`` is zero).  Returns 404 if the medication does not exist.
    """
    from butlers.tools.health import medication_history
    from butlers.tools.health._medication_utils import frequency_to_doses_per_day

    pool = _pool(db)

    # Determine the effective window boundaries and duration in days.
    # Normalise all datetimes to UTC-aware so arithmetic never raises TypeError
    # when a query param arrives without timezone info (e.g. "?start=2026-01-01T00:00:00").
    now = datetime.now(UTC)
    effective_end = end if end is not None else now
    if effective_end.tzinfo is None:
        effective_end = effective_end.replace(tzinfo=UTC)

    if start is not None:
        effective_start = start if start.tzinfo is not None else start.replace(tzinfo=UTC)
        if effective_start > effective_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start cannot be after end",
            )
        window = max((effective_end - effective_start).total_seconds() / 86400, 1.0)
    else:
        days = window_days if window_days is not None else _DEFAULT_ADHERENCE_WINDOW_DAYS
        window = float(days)
        effective_start = effective_end - timedelta(days=days)

    try:
        result = await medication_history(
            pool,
            medication_id,
            start_date=effective_start,
            end_date=effective_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    doses = result.get("doses") or []
    total = len(doses)
    skipped = sum(1 for d in doses if d.get("skipped"))
    taken = total - skipped

    # Compute expected doses from the prescribed frequency over the window.
    medication = result.get("medication") or {}
    frequency = medication.get("frequency") or "daily"
    doses_per_day = frequency_to_doses_per_day(frequency)
    expected = round(doses_per_day * window)

    adherence_rate: float | None = None
    if expected > 0:
        adherence_rate = round(taken / expected * 100, 1)

    return MedicationAdherenceResponse(
        medication_id=str(medication_id),
        total_doses=total,
        taken_doses=taken,
        skipped_doses=skipped,
        expected_doses=expected,
        adherence_rate=adherence_rate,
    )


# ---------------------------------------------------------------------------
# POST/PUT/DELETE /medications — direct dashboard CRUD
#
# These mutations persist through the SAME fact-store path the Health butler's
# own MCP tools use:
#   - POST   -> medication_add     (predicate 'medication', property fact)
#   - PUT    -> medication_update  (superseding store_fact, same subject key)
#   - DELETE -> medication_delete  (forget_memory -> validity = 'retracted')
# so a dashboard-authored medication is indistinguishable from a butler-authored
# one and is read back by GET /medications above.  No new predicates, tables, or
# DDL are introduced.
# ---------------------------------------------------------------------------


def _isoformat(value: object) -> str:
    """Coerce a datetime (or already-string) timestamp to an ISO-8601 string."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _medication_response(result: dict) -> Medication:
    """Build the Medication response model from a write-tool result dict."""
    return Medication(
        id=str(result["id"]),
        name=result.get("name", ""),
        dosage=result.get("dosage", ""),
        frequency=result.get("frequency", ""),
        schedule=list(result.get("schedule") or []),
        active=bool(result.get("active", True)),
        notes=result.get("notes"),
        created_at=_isoformat(result.get("created_at")),
        updated_at=_isoformat(result.get("updated_at") or result.get("created_at")),
    )


@router.post("/medications", response_model=Medication, status_code=status.HTTP_201_CREATED)
async def create_medication(
    body: MedicationCreateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Medication:
    """Create a medication via the butler's ``medication_add`` fact-store path.

    Writes a property fact (predicate = ``medication``, scope = ``health``) — the
    same surface the ``medication_add`` MCP tool writes — so the new medication
    appears in GET /medications immediately.
    """
    from butlers.tools.health import medication_add

    pool = _pool(db)
    result = await medication_add(
        pool,
        name=body.name,
        dosage=body.dosage,
        frequency=body.frequency,
        schedule=body.schedule,
        notes=body.notes,
    )
    return _medication_response(result)


@router.put("/medications/{medication_id}", response_model=Medication)
async def update_medication(
    medication_id: str,
    body: MedicationUpdateRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> Medication:
    """Update a medication via the superseding ``medication_update`` fact path.

    Only the supplied (non-null) fields are merged into the existing medication
    fact; a new property fact is written under the same subject key so the prior
    fact is superseded.  Returns 404 if the medication does not exist and 422 if
    no updatable fields were provided.
    """
    from butlers.tools.health import medication_update

    pool = _pool(db)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=422,
            detail="No updatable fields provided",
        )
    try:
        result = await medication_update(pool, medication_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _medication_response(result)


@router.delete("/medications/{medication_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_medication(
    medication_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete a medication via ``medication_delete`` (validity = retracted).

    The fact is retained for audit but excluded from all active read surfaces.
    Returns 204 on success and 404 if no active medication with this id exists.
    """
    from butlers.tools.health import medication_delete

    pool = _pool(db)
    try:
        await medication_delete(pool, medication_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


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
# POST/PUT/DELETE /conditions — direct dashboard CRUD
#
# These mutations persist through the SAME fact-store path the Health butler's
# own MCP tools use:
#   - POST   -> condition_add     (predicate 'condition', property fact)
#   - PUT    -> condition_update  (superseding store_fact, same subject key)
#   - DELETE -> condition_delete  (forget_memory -> validity = 'retracted')
# so a dashboard-authored condition is indistinguishable from a butler-authored
# one and is read back by GET /conditions above.  Supersession is keyed on the
# (subject, predicate) pair — condition_add/condition_update deliberately omit
# entity_id so each condition's edits supersede only that condition's prior
# fact, never every condition anchored to the owner entity.  No new predicates,
# tables, or DDL are introduced.
# ---------------------------------------------------------------------------


def _condition_response(result: dict) -> Condition:
    """Build the Condition response model from a write-tool result dict."""
    diagnosed_at = result.get("diagnosed_at")
    return Condition(
        id=str(result["id"]),
        name=result.get("name", ""),
        status=result.get("status", "active"),
        diagnosed_at=_isoformat(diagnosed_at) if diagnosed_at is not None else None,
        notes=result.get("notes"),
        created_at=_isoformat(result.get("created_at")),
        updated_at=_isoformat(result.get("updated_at") or result.get("created_at")),
    )


@router.post("/conditions", response_model=Condition, status_code=status.HTTP_201_CREATED)
async def create_condition(
    body: ConditionCreateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Condition:
    """Create a condition via the butler's ``condition_add`` fact-store path.

    Writes a property fact (predicate = ``condition``, scope = ``health``) — the
    same surface the ``condition_add`` MCP tool writes — so the new condition
    appears in GET /conditions immediately.
    """
    from butlers.tools.health import condition_add

    pool = _pool(db)
    result = await condition_add(
        pool,
        name=body.name,
        status=body.status,
        diagnosed_at=body.diagnosed_at,
        notes=body.notes,
    )
    return _condition_response(result)


@router.put("/conditions/{condition_id}", response_model=Condition)
async def update_condition(
    condition_id: str,
    body: ConditionUpdateRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> Condition:
    """Update a condition via the superseding ``condition_update`` fact path.

    Only the supplied (non-null) fields are merged into the existing condition
    fact; a new property fact is written under the same subject key so the prior
    fact is superseded.  Returns 404 if the condition does not exist and 422 if
    no updatable fields were provided.
    """
    from butlers.tools.health import condition_update

    pool = _pool(db)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=422,
            detail="No updatable fields provided",
        )
    try:
        result = await condition_update(pool, condition_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _condition_response(result)


@router.delete("/conditions/{condition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_condition(
    condition_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete a condition via ``condition_delete`` (validity = retracted).

    The fact is retained for audit but excluded from all active read surfaces.
    Returns 204 on success and 404 if no active condition with this id exists.
    """
    from butlers.tools.health import condition_delete

    pool = _pool(db)
    try:
        await condition_delete(pool, condition_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


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
# POST/PUT/DELETE /symptoms — direct dashboard CRUD
#
# These mutations persist through the SAME fact-store path the Health butler's
# own MCP tools use:
#   - POST   -> symptom_log     (predicate 'symptom', TEMPORAL fact)
#   - PUT    -> symptom_update  (in-place UPDATE of the existing symptom fact)
#   - DELETE -> symptom_delete  (forget_memory -> validity = 'retracted')
# so a dashboard-logged symptom is indistinguishable from a butler-logged one
# and is read back by GET /symptoms above.  Symptoms are TEMPORAL facts:
# ``occurred_at`` becomes the fact's ``valid_at`` and multiple entries coexist
# by design — there is NO supersession (unlike conditions/medications), so the
# update path edits the existing fact row in place rather than re-storing.  No
# new predicates, tables, or DDL are introduced.
# ---------------------------------------------------------------------------


def _symptom_response(result: dict) -> Symptom:
    """Build the Symptom response model from a write-tool result dict."""
    severity = result.get("severity")
    cond_id = result.get("condition_id")
    return Symptom(
        id=str(result["id"]),
        name=result.get("name", ""),
        severity=int(severity) if severity is not None else 0,
        condition_id=str(cond_id) if cond_id else None,
        occurred_at=_isoformat(result.get("occurred_at")),
        notes=result.get("notes"),
        created_at=_isoformat(result.get("created_at")),
    )


@router.post("/symptoms", response_model=Symptom, status_code=status.HTTP_201_CREATED)
async def create_symptom(
    body: SymptomCreateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Symptom:
    """Log a symptom via the butler's ``symptom_log`` fact-store path.

    Writes a temporal fact (predicate = ``symptom``, scope = ``health``,
    ``valid_at`` = occurred_at) — the same surface the ``symptom_log`` MCP tool
    writes — so the new symptom appears in GET /symptoms immediately.  Returns
    404 if ``condition_id`` references a non-existent condition.
    """
    from butlers.tools.health import symptom_log

    pool = _pool(db)
    try:
        result = await symptom_log(
            pool,
            name=body.name,
            severity=body.severity,
            condition_id=body.condition_id,
            notes=body.notes,
            occurred_at=body.occurred_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _symptom_response(result)


@router.put("/symptoms/{symptom_id}", response_model=Symptom)
async def update_symptom(
    symptom_id: str,
    body: SymptomUpdateRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> Symptom:
    """Update a symptom via the in-place ``symptom_update`` fact path.

    Only the supplied (non-null) fields are applied to the existing symptom
    fact.  Because symptoms are temporal facts, the edit updates the existing
    row in place (rather than superseding it).  Returns 404 if the symptom does
    not exist and 422 if no updatable fields were provided.
    """
    from butlers.tools.health import symptom_update

    pool = _pool(db)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=422,
            detail="No updatable fields provided",
        )
    try:
        result = await symptom_update(pool, symptom_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _symptom_response(result)


@router.delete("/symptoms/{symptom_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_symptom(
    symptom_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete a symptom via ``symptom_delete`` (validity = retracted).

    The fact is retained for audit but excluded from all active read surfaces.
    Returns 204 on success and 404 if no active symptom with this id exists.
    """
    from butlers.tools.health import symptom_delete

    pool = _pool(db)
    try:
        await symptom_delete(pool, symptom_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


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
# POST/PUT/DELETE /meals — direct dashboard CRUD
#
# These mutations persist through the SAME fact-store path the Health butler's
# own MCP tools use:
#   - POST   -> meal_log     (predicate 'meal_{type}', TEMPORAL fact)
#   - PUT    -> meal_update  (in-place UPDATE of the existing meal fact)
#   - DELETE -> meal_delete  (forget_memory -> validity = 'retracted')
# so a dashboard-logged meal is indistinguishable from a butler-logged one and
# is read back by GET /meals above.  Meals are TEMPORAL facts: ``eaten_at``
# becomes the fact's ``valid_at`` and multiple entries coexist by design —
# there is NO supersession (unlike conditions/medications), so the update path
# edits the existing fact row in place rather than re-storing.  The ``facts``
# table has no ``updated_at`` column.  No new predicates, tables, or DDL are
# introduced.
# ---------------------------------------------------------------------------


def _meal_response(result: dict) -> Meal:
    """Build the Meal response model from a write-tool result dict.

    The ``meal_log`` / ``meal_update`` tools return ``estimated_calories`` and a
    ``macros`` dict; reshape those into the same ``nutrition`` envelope GET
    /meals returns so create/update responses match the list surface.
    """
    estimated_calories = result.get("estimated_calories")
    macros = result.get("macros") or {}
    if isinstance(macros, str):
        macros = json.loads(macros)
    has_nutrition = estimated_calories is not None or any(
        macros.get(k) is not None for k in ("protein_g", "carbs_g", "fat_g")
    )
    nutrition = (
        {
            "calories": estimated_calories,
            "protein_g": macros.get("protein_g"),
            "carbs_g": macros.get("carbs_g"),
            "fat_g": macros.get("fat_g"),
        }
        if has_nutrition
        else None
    )
    return Meal(
        id=str(result["id"]),
        type=result.get("type", ""),
        description=result.get("description", ""),
        nutrition=nutrition,
        eaten_at=_isoformat(result.get("eaten_at")),
        notes=result.get("notes"),
        created_at=_isoformat(result.get("created_at")),
    )


@router.post("/meals", response_model=Meal, status_code=status.HTTP_201_CREATED)
async def create_meal(
    body: MealCreateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Meal:
    """Log a meal via the butler's ``meal_log`` fact-store path.

    Writes a temporal fact (predicate = ``meal_{type}``, scope = ``health``,
    ``valid_at`` = eaten_at) — the same surface the ``meal_log`` MCP tool
    writes — so the new meal appears in GET /meals immediately.  Returns 422 if
    the meal type is invalid.
    """
    from butlers.tools.health import meal_log

    pool = _pool(db)
    try:
        result = await meal_log(
            pool,
            type=body.type,
            description=body.description,
            eaten_at=body.eaten_at,
            nutrition=body.nutrition,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _meal_response(result)


@router.put("/meals/{meal_id}", response_model=Meal)
async def update_meal(
    meal_id: str,
    body: MealUpdateRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> Meal:
    """Update a meal via the in-place ``meal_update`` fact path.

    Only the supplied (non-null) fields are applied to the existing meal fact.
    Because meals are temporal facts, the edit updates the existing row in place
    (rather than superseding it).  Returns 404 if the meal does not exist and
    422 if no updatable fields were provided.
    """
    from butlers.tools.health import meal_update

    pool = _pool(db)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=422,
            detail="No updatable fields provided",
        )
    try:
        result = await meal_update(pool, meal_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _meal_response(result)


@router.delete("/meals/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal(
    meal_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete a meal via ``meal_delete`` (validity = retracted).

    The fact is retained for audit but excluded from all active read surfaces.
    Returns 204 on success and 404 if no active meal with this id exists.
    """
    from butlers.tools.health import meal_delete

    pool = _pool(db)
    try:
        await meal_delete(pool, meal_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


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
# POST/PUT/DELETE /research — direct dashboard CRUD
#
# These mutations persist through the SAME fact-store path the Health butler's
# own MCP tools use:
#   - POST   -> research_save    (predicate 'research', PROPERTY fact)
#   - PUT    -> research_update  (superseding store_fact, same subject key)
#   - DELETE -> research_delete  (forget_memory -> validity = 'retracted')
# so a dashboard-authored research note is indistinguishable from a
# butler-authored one and is read back by GET /research above.  Research notes
# are PROPERTY facts (like conditions, NOT temporal like symptoms/meals):
# supersession is keyed on the ``research:{title}`` subject, so an edit
# supersedes only THIS note's prior fact, never every note anchored to the owner
# entity (research_save/research_update deliberately omit entity_id).  No new
# predicates, tables, or DDL are introduced.
# ---------------------------------------------------------------------------


def _research_response(result: dict) -> Research:
    """Build the Research response model from a write-tool result dict."""
    cond_id = result.get("condition_id")
    return Research(
        id=str(result["id"]),
        title=result.get("title", ""),
        content=result.get("content", ""),
        tags=list(result.get("tags") or []),
        source_url=result.get("source_url"),
        condition_id=str(cond_id) if cond_id else None,
        created_at=_isoformat(result.get("created_at")),
        updated_at=_isoformat(result.get("updated_at") or result.get("created_at")),
    )


@router.post("/research", response_model=Research, status_code=status.HTTP_201_CREATED)
async def create_research(
    body: ResearchCreateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Research:
    """Create a research note via the butler's ``research_save`` fact-store path.

    Writes a property fact (predicate = ``research``, scope = ``health``) — the
    same surface the ``research_save`` MCP tool writes — so the new note appears
    in GET /research immediately.  Returns 404 if ``condition_id`` references a
    non-existent condition.
    """
    from butlers.tools.health import research_save

    pool = _pool(db)
    try:
        result = await research_save(
            pool,
            title=body.title,
            content=body.content,
            tags=body.tags,
            source_url=body.source_url,
            condition_id=body.condition_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _research_response(result)


@router.put("/research/{research_id}", response_model=Research)
async def update_research(
    research_id: str,
    body: ResearchUpdateRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> Research:
    """Update a research note via the superseding ``research_update`` fact path.

    Only the supplied (non-null) fields are merged into the existing research
    fact; a new property fact is written under the same subject key so the prior
    fact is superseded.  Returns 404 if the note does not exist and 422 if no
    updatable fields were provided.
    """
    from butlers.tools.health import research_update

    pool = _pool(db)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=422,
            detail="No updatable fields provided",
        )
    try:
        result = await research_update(pool, research_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _research_response(result)


@router.delete("/research/{research_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_research(
    research_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete a research note via ``research_delete`` (validity = retracted).

    The fact is retained for audit but excluded from all active read surfaces.
    Returns 204 on success and 404 if no active research note with this id exists.
    """
    from butlers.tools.health import research_delete

    pool = _pool(db)
    try:
        await research_delete(pool, research_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


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


# ---------------------------------------------------------------------------
# GET /nutrition/summary — aggregate nutrition over a date range
#
# Thin HTTP wrapper over the existing `nutrition_summary(pool, start, end)` diet
# tool, which aggregates `meal_*` facts (scope = 'health') that carry nutrition
# metadata.  Read-only; reads the same surface the `meal_log` MCP tool writes.
# No new predicates, tables, or DDL are introduced.
# ---------------------------------------------------------------------------


@router.get("/nutrition/summary", response_model=NutritionSummaryResponse)
async def get_nutrition_summary(
    start: datetime = Query(..., description="Window start (inclusive)"),
    end: datetime = Query(..., description="Window end (inclusive)"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> NutritionSummaryResponse:
    """Return aggregate nutrition totals and daily averages over a window.

    Aggregates ``meal_*`` facts with nutrition metadata (the same surface the
    ``meal_log`` MCP tool writes) via the ``nutrition_summary`` diet tool.
    Meals without nutrition data are excluded.  ``days`` is the inclusive span
    used to compute the daily averages (minimum 1).
    """
    from butlers.tools.health import nutrition_summary

    pool = _pool(db)
    result = await nutrition_summary(pool, start, end)
    days = max((end - start).days, 1)

    return NutritionSummaryResponse(
        total_calories=result["total_calories"],
        total_protein_g=result["total_protein_g"],
        total_carbs_g=result["total_carbs_g"],
        total_fat_g=result["total_fat_g"],
        daily_avg=NutritionDailyAverage(
            calories=result["daily_avg_calories"],
            protein_g=result["daily_avg_protein_g"],
            carbs_g=result["daily_avg_carbs_g"],
            fat_g=result["daily_avg_fat_g"],
        ),
        meal_count=result["meal_count"],
        days=days,
    )


# ===========================================================================
# GET /briefing — health Voice briefing (mirrors GET /api/dashboard/briefing)
#
# An owner-only LLM Voice composer over the health summary + the proactive
# insight feed.  It is templated-only BY DEFAULT; an LLM elaboration is
# attempted only when the cost flag is on, and even then it falls through to
# the deterministic templated paragraph on ANY failure (LLM error, timeout,
# empty text, or a non-diagnostic voice-lint rejection).  ``source`` is exactly
# "llm" or "fallback".  The endpoint NEVER raises in normal operation.
#
# Owner gating, the per-owner 5-minute TTL cache, and the elaborate->lint->
# fallback pipeline are copied from src/butlers/api/routers/dashboard_briefing.py.
#
# Voice: the briefing is descriptive and NON-DIAGNOSTIC.  It pairs measurements
# with the owner's own stored reference ranges rather than rendering clinical
# verdicts, and never frames co-occurring signals as cause and effect.
# ---------------------------------------------------------------------------

SWITCHBOARD_DB = "switchboard"

# Insight priority at or above this threshold is treated as "needs review now".
_HIGH_INSIGHT_PRIORITY = 2

# Runtime butler name used for the briefing's discretion-tier LLM dispatch.
HEALTH_BRIEFING_RUNTIME_BUTLER_NAME = "__health_briefing__"


# --- Cost flag -------------------------------------------------------------
#
# Templated-only by default.  LLM elaboration is enabled only behind a cost
# flag, mirroring how the dashboard briefing keeps the LLM path gated behind a
# config/catalog mechanism.  The flag is read fresh on every request so it can
# be toggled without a restart (and monkeypatched in tests).


def _health_briefing_llm_enabled() -> bool:
    """Return True only when the LLM-elaboration cost flag is explicitly on.

    Off by default so the endpoint is templated-only and invokes no LLM unless
    an operator opts into the cost. Set ``HEALTH_BRIEFING_LLM_ENABLED`` to one
    of ``1/true/yes/on`` to enable.
    """
    return os.environ.get("HEALTH_BRIEFING_LLM_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# --- Owner gate (mirrors dashboard_briefing._assert_owner_contact) ----------


async def _assert_owner_contact(pool: Any) -> Any:
    """Raise HTTP 403 unless an owner entity exists; return its id as cache key.

    Mirrors the dashboard briefing owner gate: asserts ``'owner' = ANY(roles)``
    on ``public.entities``. In v1 the dashboard is owner-only and there is no
    per-request identity, so the assertion checks that the system is
    bootstrapped with an owner entity.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT id
            FROM public.entities
            WHERE 'owner' = ANY(roles)
            LIMIT 1
            """
        )
    except Exception as exc:
        logger.warning("Health briefing owner-entity assertion query failed: %s", exc)
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Owner contact assertion failed"},
        )

    if row is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Owner contact not found"},
        )

    return row["id"]


async def _owner_local_now(pool: Any, *, utc_now: datetime | None = None) -> datetime:
    """Return the current wall-clock time in the owner's configured timezone."""
    current = utc_now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    try:
        settings = await load_general_settings(pool)
        timezone_name = str(settings.get("timezone") or "UTC")
        return current.astimezone(ZoneInfo(timezone_name))
    except Exception as exc:
        logger.warning("Could not resolve owner timezone for health briefing: %s", exc)
        return current.astimezone(UTC)


# --- Non-diagnostic voice lint (extends the global voice_lint_passes) -------
#
# The briefing copy must read as a calm, descriptive log entry — never a
# diagnosis, a prediction, or a celebration. These rules extend the shared
# dashboard voice lint (no exclamation/em-dash/first-person/future-tense/
# hedging) with health-specific bans.

# (1) Diagnosis / medical-advice tokens.
_HEALTH_DIAGNOSIS = re.compile(
    r"(diagnos\w*"
    r"|\brisk of\b"
    r"|\bsymptom of\b"
    r"|\bconsistent with\b"
    r"|\bindicates?\b"
    r"|\byou (?:may|might|could) have\b"
    r"|\bshould see (?:a|your) (?:doctor|physician|provider)\b)",
    re.IGNORECASE,
)

# (2) Causal connectives — the briefing states co-occurrence, never causation.
_HEALTH_CAUSAL = re.compile(
    r"\b(because(?: of)?|caused? by|causes?|due to|leads? to|results? in|triggers?)\b",
    re.IGNORECASE,
)

# (3) Celebration / judgment — praise, streak/green-check, encouragement.
_HEALTH_PRAISE = re.compile(
    r"(\b(great job|well done|amazing|awesome|congrat\w*|keep it up|nice work|proud"
    r"|on track|streak)\b|✅|✓)",
    re.IGNORECASE,
)

# (4) Future-tense markers — prediction is diagnosis-adjacent. The global lint
# only bans "will be"/"is going to"; here we also ban bare "will"/"going to".
_HEALTH_FUTURE = re.compile(r"\b(will|going to|gonna)\b", re.IGNORECASE)

# (5) Clinical verdict adjectives — pair a measurement with the owner's own
# stored reference range instead of rendering a verdict on it.
_HEALTH_VERDICT = re.compile(
    r"\b(elevated|dangerously|abnormal|critically|too high|too low|out of range|alarming)\b",
    re.IGNORECASE,
)

_HEALTH_LINT_RULES: list[tuple[str, re.Pattern]] = [
    ("diagnosis_or_advice", _HEALTH_DIAGNOSIS),
    ("causal_connective", _HEALTH_CAUSAL),
    ("celebration_or_judgment", _HEALTH_PRAISE),
    ("future_tense", _HEALTH_FUTURE),
    ("clinical_verdict", _HEALTH_VERDICT),
]


def _health_first_violation(text: str) -> str | None:
    """Return the label of the first violated rule (global or health), else None."""
    base = first_violation(text)
    if base is not None:
        return base
    for label, pattern in _HEALTH_LINT_RULES:
        if pattern.search(text):
            return label
    return None


def _health_voice_lint_passes(text: str) -> bool:
    """Return True only if *text* passes the global AND the health-specific lint."""
    if not voice_lint_passes(text):
        return False
    return not any(pattern.search(text) for _label, pattern in _HEALTH_LINT_RULES)


# --- State fetch -----------------------------------------------------------


async def _fetch_health_summary(health_pool: Any) -> dict:
    """Fetch the health summary (measurements/medications/conditions).

    Returns an empty-shaped summary on any failure; logs at WARNING.
    """
    try:
        from butlers.tools.health import health_summary

        return await health_summary(health_pool)
    except Exception as exc:
        logger.warning("Could not fetch health summary for briefing: %s", exc)
        return {
            "recent_measurements": [],
            "active_medications": [],
            "active_conditions": [],
        }


async def _fetch_health_insights(sw_pool: Any) -> list[dict]:
    """Fetch pending health insight candidates from the Switchboard insight feed.

    Reads ``public.insight_candidates`` (origin_butler = 'health', status =
    'pending') — the same surface the GET /api/insights Switchboard reader
    exposes. Only the Switchboard role has SELECT on this table, so the
    Switchboard pool is used. Returns [] on any failure; logs at WARNING.
    """
    try:
        rows = await sw_pool.fetch(
            """
            SELECT id, category, priority, message, metadata, created_at, status, expires_at
            FROM public.insight_candidates
            WHERE status = 'pending' AND origin_butler = $1
            ORDER BY priority DESC, created_at ASC
            LIMIT 50
            """,
            "health",
        )
    except Exception as exc:
        logger.warning("Could not fetch health insight feed for briefing: %s", exc)
        return []

    insights: list[dict] = []
    for r in rows:
        try:
            priority = int(r["priority"])
        except (TypeError, ValueError, KeyError):
            priority = 0
        insights.append(
            {
                "id": str(r["id"]),
                "category": r["category"],
                "priority": priority,
                "message": r["message"],
                "created_at": (r["created_at"].isoformat() if r["created_at"] else None),
            }
        )
    return insights


async def _fetch_health_briefing_state(health_pool: Any, sw_pool: Any, now: datetime) -> dict:
    """Build the internal state used for classification and prose.

    Composes the health summary (from the health pool) with the proactive
    insight feed (from the Switchboard pool). The public response is still the
    six-field Briefing object; this richer state stays internal.
    """
    summary = await _fetch_health_summary(health_pool)
    insights = await _fetch_health_insights(sw_pool)
    return {
        "now": now,
        "summary": summary,
        "insights": insights,
    }


# --- Classification / headline / fallback ----------------------------------


def _classify_health(state: dict) -> str:
    """Classify health state from the insight feed (count-based, non-diagnostic).

    Priority (top wins):
        attention  1+ pending insight at or above the high-priority threshold
        active     3+ pending insights, none high
        light      1-2 pending insights, none high
        quiet      0 pending insights
    """
    insights: list[dict] = state.get("insights", [])
    high = sum(1 for i in insights if int(i.get("priority", 0)) >= _HIGH_INSIGHT_PRIORITY)
    total = len(insights)

    if high >= 1:
        return "attention"
    if total >= 3:
        return "active"
    if total >= 1:
        return "light"
    return "quiet"


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _health_headline(state: dict, state_class: str) -> str:
    """Return the non-diagnostic headline body for a given health state class."""
    insights: list[dict] = state.get("insights", [])
    high = sum(1 for i in insights if int(i.get("priority", 0)) >= _HIGH_INSIGHT_PRIORITY)
    total = len(insights)

    if state_class == "attention":
        n = high if high else 1
        verb = _plural(n, "insight needs", "insights need")
        return f"{n} health {verb} review."
    if state_class == "active":
        return f"The health feed holds {total} insights to review."
    if state_class == "light":
        noun = _plural(total, "insight is", "insights are")
        return f"{total} health {noun} noted since the last review."
    return "The health log is steady."


def _health_fallback(state: dict, state_class: str) -> str:
    """Return the deterministic templated paragraph (factual counts only).

    Purely descriptive — it reports counts from the health log and the insight
    feed and renders no clinical verdict, prediction, or judgment, so it always
    passes the non-diagnostic voice lint.
    """
    summary = state.get("summary", {})
    meds = len(summary.get("active_medications", []) or [])
    conds = len(summary.get("active_conditions", []) or [])
    meas = len(summary.get("recent_measurements", []) or [])
    ins = len(state.get("insights", []) or [])

    med_word = _plural(meds, "medication", "medications")
    cond_word = _plural(conds, "condition", "conditions")
    meas_word = _plural(meas, "measurement type", "measurement types")
    ins_word = _plural(ins, "item", "items")

    return (
        f"The health log holds {meds} active {med_word} and {conds} tracked {cond_word}. "
        f"{meas} {meas_word} carry recent readings, and the insight feed has "
        f"{ins} {ins_word} pending review."
    )


# --- LLM elaboration (gated by the cost flag; never raises to the caller) ---

_HEALTH_SYSTEM_PROMPT = """\
You write a one-to-three sentence elaboration paragraph for a personal health \
dashboard. The paragraph names what is true about the health log and the insight \
feed right now, drawing only from the state JSON provided in the user message.

This is a descriptive log entry, never medical guidance. Hard rules (all mandatory):
- No diagnosis and no advice. Do not name conditions, do not write "risk of," \
"symptom of," "consistent with," "indicates," "you may have," or "should see a doctor."
- No causation. State that signals co-occurred; never write that one caused another \
("because," "due to," "leads to," "caused by").
- No clinical verdicts on a number. Do not write "elevated," "too high," "abnormal," \
or "dangerously." Pair a reading with the owner's own stored reference range instead.
- No prediction. No future tense. Do not write "will" or "going to."
- No celebration or judgment. No exclamation marks, no praise, no "streak" or "on track."
- No first person. Write "the log," "the feed." Never "I," "we," "us," or "our."
- No em-dashes. Maximum 50 words. Three sentences at most.
- Write only the paragraph. No preamble, no sign-off, no markdown formatting.
"""


def _compact_insight(item: dict) -> dict:
    return {
        "category": item.get("category"),
        "priority": item.get("priority"),
        "message": item.get("message"),
    }


def _build_health_user_message(state: dict, state_class: str) -> str:
    """Render the user turn from health state and the computed class."""
    summary = state.get("summary", {})
    insights = state.get("insights", [])
    state_summary = {
        "state_class": state_class,
        "generated_for_local_time": state.get("now"),
        "counts": {
            "active_medications": len(summary.get("active_medications", []) or []),
            "active_conditions": len(summary.get("active_conditions", []) or []),
            "recent_measurement_types": len(summary.get("recent_measurements", []) or []),
            "pending_insights": len(insights),
        },
        "recent_measurements": summary.get("recent_measurements", []),
        "top_insights": [_compact_insight(i) for i in insights[:5]],
    }
    return (
        f"Health state:\n{json.dumps(state_summary, default=str, indent=2)}\n\n"
        f"Write the elaboration paragraph for state_class={state_class!r}."
    )


async def elaborate_health_llm(pool: Any, state: dict, state_class: str) -> str | None:
    """Call the catalog-backed local runtime and return the paragraph or None.

    Returns None on any failure so the caller uses the deterministic fallback.
    """
    dispatcher = DiscretionDispatcher(
        pool,
        butler_name=HEALTH_BRIEFING_RUNTIME_BUTLER_NAME,
        complexity_tier=Complexity.CHEAP,
    )
    try:
        text = (
            await dispatcher.call(
                _build_health_user_message(state, state_class),
                system_prompt=_HEALTH_SYSTEM_PROMPT,
            )
        ).strip()
        if not text:
            logger.info("Health briefing LLM elaboration returned empty text")
            return None
        return text
    except Exception as exc:
        logger.warning("Health briefing local-runtime elaboration failed: %s", exc)
        return None


# --- Composition -----------------------------------------------------------


async def _compose_health_briefing(
    state: dict,
    cache: BriefingCache,
    owner_id: Any,
    llm_pool: Any,
) -> dict:
    """Compose a fresh Briefing dict and populate the cache.

    Pipeline: classify -> greet+headline -> (cost-flag) LLM elaboration ->
    non-diagnostic voice lint -> templated fallback on any failure -> cache.
    Every fallback path logs its reason with structured context so a silently
    degraded (always-templated) briefing is diagnosable rather than invisible.
    """
    now = state["now"]

    try:
        state_class = _classify_health(state)
    except Exception as exc:
        logger.error("Health briefing classification failed, defaulting to quiet: %s", exc)
        state_class = "quiet"

    hour = now.hour if isinstance(now, datetime) else 12
    greet = f"Good {time_of_day(hour)}."
    headline = _health_headline(state, state_class)

    elaboration: str | None = None
    source = "fallback"

    if _health_briefing_llm_enabled():
        try:
            llm_text = await elaborate_health_llm(llm_pool, state, state_class)
            if llm_text:
                if _health_voice_lint_passes(llm_text):
                    elaboration = llm_text
                    source = "llm"
                else:
                    violation = _health_first_violation(llm_text)
                    logger.info(
                        "Health briefing LLM elaboration rejected by voice lint "
                        "(violation=%s, state_class=%s); using templated fallback",
                        violation,
                        state_class,
                    )
            else:
                logger.info(
                    "Health briefing LLM produced no text (state_class=%s); "
                    "using templated fallback",
                    state_class,
                )
        except Exception as exc:
            logger.warning(
                "Health briefing LLM elaboration raised (state_class=%s); "
                "using templated fallback: %s",
                state_class,
                exc,
            )
    else:
        logger.debug(
            "Health briefing LLM disabled by cost flag (state_class=%s); templated fallback",
            state_class,
        )

    if elaboration is None:
        elaboration = _health_fallback(state, state_class)

    generated_at = datetime.now(UTC).isoformat()

    briefing_dict = {
        "greet": greet,
        "headline": headline,
        "elaboration": elaboration,
        "source": source,
        "state_class": state_class,
        "generated_at": generated_at,
    }

    cache.set(owner_id, briefing_dict)
    return briefing_dict


@router.get("/briefing", response_model=ApiResponse[Briefing])
async def get_health_briefing(
    db: DatabaseManager = Depends(_get_db_manager),
    cache: BriefingCache = Depends(get_health_briefing_cache),
) -> ApiResponse[Briefing]:
    """Return the health Voice briefing for the authenticated owner.

    - Owner-only: HTTP 403 for non-owner (no cache read or write), 401 for
      unauthenticated (via the API-key middleware).
    - Templated-only BY DEFAULT; an LLM elaboration is attempted only when the
      cost flag is on, and falls through to the templated fallback on any
      failure (LLM error, timeout, empty text, or voice-lint rejection).
    - ``source`` is exactly "llm" or "fallback".
    - 5-minute per-owner cache: a cache hit preserves the original generated_at.
    - Never raises HTTP 500 in normal operation.
    """
    try:
        sw_pool = db.pool(SWITCHBOARD_DB)
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")
    health_pool = _pool(db)

    # Owner-only gate (HTTP 403 for non-owner) — before any cache read or write.
    owner_id = await _assert_owner_contact(sw_pool)

    cached = cache.get(owner_id)
    if cached is not None:
        return ApiResponse(data=Briefing(**cached))

    now = await _owner_local_now(sw_pool)
    state = await _fetch_health_briefing_state(health_pool, sw_pool, now)
    briefing_dict = await _compose_health_briefing(state, cache, owner_id, sw_pool)
    return ApiResponse(data=Briefing(**briefing_dict))
