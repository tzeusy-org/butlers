"""Home butler endpoints.

Provides endpoints for Home Assistant entity state, areas, command audit log,
and snapshot freshness. All data is queried directly from the home butler's
PostgreSQL schema via asyncpg.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("home_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["home_api_models"] = _models
    _spec.loader.exec_module(_models)

    AreaResponse = _models.AreaResponse
    CommandLogEntry = _models.CommandLogEntry
    EntityStateResponse = _models.EntityStateResponse
    EntitySummaryResponse = _models.EntitySummaryResponse
    StatisticsResponse = _models.StatisticsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/home", tags=["home"])

BUTLER_DB = "home"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the home butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Home butler database is not available",
        )


# ---------------------------------------------------------------------------
# GET /api/home/entities — list entities with optional domain/area filters
# ---------------------------------------------------------------------------


@router.get("/entities", response_model=PaginatedResponse[EntitySummaryResponse])
async def list_entities(
    domain: str | None = Query(None, description="Filter by HA domain (e.g. 'light', 'switch')"),
    area: str | None = Query(None, description="Filter by area_id stored in entity attributes"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[EntitySummaryResponse]:
    """List Home Assistant entities from the snapshot cache.

    Supports optional filtering by domain (derived from entity_id prefix)
    and area (derived from ``attributes->>'area_id'``).
    """
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if domain is not None:
        conditions.append(f"entity_id LIKE ${idx} || '.%%'")
        args.append(domain)
        idx += 1

    if area is not None:
        conditions.append(f"attributes->>'area_id' = ${idx}")
        args.append(area)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM ha_entity_snapshot{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT entity_id, state, attributes, last_updated, captured_at"
        f" FROM ha_entity_snapshot{where}"
        f" ORDER BY entity_id"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        EntitySummaryResponse(
            entity_id=r["entity_id"],
            state=r["state"],
            friendly_name=(
                dict(r["attributes"] or {}).get("friendly_name") if r["attributes"] else None
            ),
            domain=r["entity_id"].split(".")[0] if "." in r["entity_id"] else r["entity_id"],
            last_updated=str(r["last_updated"]) if r["last_updated"] else None,
            captured_at=str(r["captured_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[EntitySummaryResponse](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/home/entities/{entity_id} — single entity detail
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id:path}", response_model=EntityStateResponse)
async def get_entity(
    entity_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> EntityStateResponse:
    """Retrieve full state detail for a single Home Assistant entity.

    Returns 404 if the entity is not in the snapshot cache.
    """
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT entity_id, state, attributes, last_updated, captured_at"
        " FROM ha_entity_snapshot"
        " WHERE entity_id = $1",
        entity_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")

    return EntityStateResponse(
        entity_id=row["entity_id"],
        state=row["state"],
        attributes=dict(row["attributes"] or {}),
        last_updated=str(row["last_updated"]) if row["last_updated"] else None,
        captured_at=str(row["captured_at"]),
    )


# ---------------------------------------------------------------------------
# GET /api/home/areas — list areas derived from entity snapshot attributes
# ---------------------------------------------------------------------------


@router.get("/areas", response_model=list[AreaResponse])
async def list_areas(
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[AreaResponse]:
    """List all areas found in the Home Assistant entity snapshot cache.

    Areas are derived from the ``area_id`` field in entity attributes JSONB.
    Only entities with a non-null ``area_id`` are included.
    """
    pool = _pool(db)

    rows = await pool.fetch(
        "SELECT attributes->>'area_id' AS area_id, count(*) AS entity_count"
        " FROM ha_entity_snapshot"
        " WHERE attributes->>'area_id' IS NOT NULL"
        " GROUP BY attributes->>'area_id'"
        " ORDER BY attributes->>'area_id'",
    )

    return [
        AreaResponse(
            area_id=r["area_id"],
            entity_count=int(r["entity_count"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /api/home/command-log — query ha_command_log with time range + pagination
# ---------------------------------------------------------------------------


@router.get("/command-log", response_model=PaginatedResponse[CommandLogEntry])
async def list_command_log(
    start: str | None = Query(
        None, description="Filter commands issued at or after this timestamp (ISO 8601)"
    ),
    end: str | None = Query(
        None, description="Filter commands issued at or before this timestamp (ISO 8601)"
    ),
    domain: str | None = Query(None, description="Filter by HA service domain"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[CommandLogEntry]:
    """Query the Home Assistant command audit log with optional time range and pagination."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if start is not None:
        conditions.append(f"issued_at >= ${idx}")
        args.append(start)
        idx += 1

    if end is not None:
        conditions.append(f"issued_at <= ${idx}")
        args.append(end)
        idx += 1

    if domain is not None:
        conditions.append(f"domain = ${idx}")
        args.append(domain)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM ha_command_log{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, domain, service, target, data, result, context_id, issued_at"
        f" FROM ha_command_log{where}"
        f" ORDER BY issued_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        CommandLogEntry(
            id=int(r["id"]),
            domain=r["domain"],
            service=r["service"],
            target=dict(r["target"]) if r["target"] else None,
            data=dict(r["data"]) if r["data"] else None,
            result=dict(r["result"]) if r["result"] else None,
            context_id=r["context_id"],
            issued_at=str(r["issued_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[CommandLogEntry](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/home/snapshot-status — entity snapshot freshness
# ---------------------------------------------------------------------------


@router.get("/snapshot-status", response_model=StatisticsResponse)
async def get_snapshot_status(
    db: DatabaseManager = Depends(_get_db_manager),
) -> StatisticsResponse:
    """Return entity snapshot freshness and aggregate statistics.

    Reports total entity count, per-domain counts, and the oldest/newest
    ``captured_at`` timestamps in the snapshot cache.
    """
    pool = _pool(db)

    total: int = await pool.fetchval("SELECT count(*) FROM ha_entity_snapshot") or 0

    # Per-domain counts (domain = prefix before first '.')
    domain_rows = await pool.fetch(
        "SELECT split_part(entity_id, '.', 1) AS domain, count(*) AS cnt"
        " FROM ha_entity_snapshot"
        " GROUP BY split_part(entity_id, '.', 1)"
        " ORDER BY split_part(entity_id, '.', 1)"
    )
    domains: dict[str, int] = {r["domain"]: int(r["cnt"]) for r in domain_rows}

    # Freshness bounds
    bounds_row = await pool.fetchrow(
        "SELECT min(captured_at) AS oldest, max(captured_at) AS newest FROM ha_entity_snapshot"
    )

    oldest = str(bounds_row["oldest"]) if bounds_row and bounds_row["oldest"] else None
    newest = str(bounds_row["newest"]) if bounds_row and bounds_row["newest"] else None

    return StatisticsResponse(
        total_entities=total,
        domains=domains,
        oldest_captured_at=oldest,
        newest_captured_at=newest,
    )
