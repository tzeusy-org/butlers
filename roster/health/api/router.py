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
    Meal = _models.Meal
    Measurement = _models.Measurement
    Medication = _models.Medication
    Research = _models.Research
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
    """List measurements with optional type and date range filters."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if type is not None:
        conditions.append(f"type = ${idx}")
        args.append(type)
        idx += 1

    if since is not None:
        conditions.append(f"measured_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"measured_at <= ${idx}")
        args.append(until)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM measurements{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, type, value, measured_at, notes, created_at"
        f" FROM measurements{where}"
        f" ORDER BY measured_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        Measurement(
            id=str(r["id"]),
            type=r["type"],
            value=dict(r["value"]) if isinstance(r["value"], dict) else json.loads(r["value"]),
            measured_at=str(r["measured_at"]),
            notes=r["notes"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]

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
# GET /meals — list meals
# ---------------------------------------------------------------------------


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

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if type is not None:
        conditions.append(f"type = ${idx}")
        args.append(type)
        idx += 1

    if since is not None:
        conditions.append(f"eaten_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"eaten_at <= ${idx}")
        args.append(until)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM meals{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, type, description, nutrition, eaten_at, notes, created_at"
        f" FROM meals{where}"
        f" ORDER BY eaten_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        Meal(
            id=str(r["id"]),
            type=r["type"],
            description=r["description"],
            nutrition=dict(r["nutrition"]) if r["nutrition"] else None,
            eaten_at=str(r["eaten_at"]),
            notes=r["notes"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]

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
        args.append(json.dumps([tag]))
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
