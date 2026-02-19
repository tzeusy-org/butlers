"""Switchboard butler endpoints.

Provides read-only endpoints for the routing log and butler registry.
All data is queried directly from the switchboard butler's PostgreSQL
database via asyncpg.

Ingestion has moved to the Switchboard MCP server's ``ingest`` tool.
"""

from __future__ import annotations

import datetime
import importlib.util
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("switchboard_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["switchboard_api_models"] = _models
    _spec.loader.exec_module(_models)

    RegistryEntry = _models.RegistryEntry
    RoutingEntry = _models.RoutingEntry
    HeartbeatRequest = _models.HeartbeatRequest
    HeartbeatResponse = _models.HeartbeatResponse
else:
    raise RuntimeError("Failed to load switchboard API models")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/switchboard", tags=["switchboard"])

BUTLER_DB = "switchboard"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the switchboard butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Switchboard butler database is not available",
        )


# ---------------------------------------------------------------------------
# GET /routing-log — paginated routing log
# ---------------------------------------------------------------------------


@router.get("/routing-log", response_model=PaginatedResponse[RoutingEntry])
async def list_routing_log(
    source_butler: str | None = Query(None, description="Filter by source butler"),
    target_butler: str | None = Query(None, description="Filter by target butler"),
    since: str | None = Query(None, description="Filter from this timestamp (inclusive)"),
    until: str | None = Query(None, description="Filter up to this timestamp (inclusive)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[RoutingEntry]:
    """List routing log entries with optional filters, paginated."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if source_butler is not None:
        conditions.append(f"source_butler = ${idx}")
        args.append(source_butler)
        idx += 1

    if target_butler is not None:
        conditions.append(f"target_butler = ${idx}")
        args.append(target_butler)
        idx += 1

    if since is not None:
        conditions.append(f"created_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"created_at <= ${idx}")
        args.append(until)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM routing_log{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, source_butler, target_butler, tool_name, success,"
        f" duration_ms, error, created_at"
        f" FROM routing_log{where}"
        f" ORDER BY created_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        RoutingEntry(
            id=str(r["id"]),
            source_butler=r["source_butler"],
            target_butler=r["target_butler"],
            tool_name=r["tool_name"],
            success=r["success"],
            duration_ms=r["duration_ms"],
            error=r["error"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[RoutingEntry](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /registry — butler registry
# ---------------------------------------------------------------------------


@router.get("/registry", response_model=ApiResponse[list[RegistryEntry]])
async def list_registry(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[RegistryEntry]]:
    """List all registered butlers from the switchboard registry."""
    pool = _pool(db)

    rows = await pool.fetch(
        "SELECT name, endpoint_url, description, modules, capabilities, last_seen_at,"
        " eligibility_state, liveness_ttl_seconds, quarantined_at, quarantine_reason,"
        " route_contract_min, route_contract_max, eligibility_updated_at, registered_at"
        " FROM butler_registry"
        " ORDER BY name",
    )

    data: list[RegistryEntry] = []
    for row in rows:
        r = dict(row)
        data.append(
            RegistryEntry(
                name=r["name"],
                endpoint_url=r["endpoint_url"],
                description=r.get("description"),
                modules=list(r["modules"]) if r.get("modules") else [],
                capabilities=list(r["capabilities"]) if r.get("capabilities") else [],
                last_seen_at=str(r["last_seen_at"]) if r.get("last_seen_at") else None,
                eligibility_state=str(r.get("eligibility_state") or "active"),
                liveness_ttl_seconds=int(r.get("liveness_ttl_seconds") or 300),
                quarantined_at=str(r["quarantined_at"]) if r.get("quarantined_at") else None,
                quarantine_reason=str(r["quarantine_reason"])
                if r.get("quarantine_reason")
                else None,
                route_contract_min=int(r.get("route_contract_min") or 1),
                route_contract_max=int(r.get("route_contract_max") or 1),
                eligibility_updated_at=str(r["eligibility_updated_at"])
                if r.get("eligibility_updated_at")
                else None,
                registered_at=str(r["registered_at"]),
            )
        )

    return ApiResponse[list[RegistryEntry]](data=data)


# ---------------------------------------------------------------------------
# POST /heartbeat — butler liveness signal
# ---------------------------------------------------------------------------


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def receive_heartbeat(
    body: HeartbeatRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> HeartbeatResponse:
    """Receive a liveness heartbeat from a butler.

    Updates ``last_seen_at`` and manages eligibility state transitions:
    - ``stale`` → ``active``: transition is logged to the eligibility audit log
    - ``quarantined``: ``last_seen_at`` is updated, state remains unchanged
    - ``active``: ``last_seen_at`` is updated, state unchanged
    """
    pool = _pool(db)

    now = datetime.datetime.now(datetime.UTC)

    row = await pool.fetchrow(
        "SELECT eligibility_state, last_seen_at FROM butler_registry WHERE name = $1",
        body.butler_name,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Butler '{body.butler_name}' not found")

    current_state: str = row["eligibility_state"]
    previous_last_seen_at = row["last_seen_at"]

    if current_state == "stale":
        # Transition stale → active and log the transition
        await pool.execute(
            "UPDATE butler_registry"
            " SET last_seen_at = $1, eligibility_state = 'active',"
            "     eligibility_updated_at = $1"
            " WHERE name = $2",
            now,
            body.butler_name,
        )
        await pool.execute(
            "INSERT INTO butler_registry_eligibility_log"
            " (butler_name, previous_state, new_state, reason,"
            "  previous_last_seen_at, new_last_seen_at, observed_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            body.butler_name,
            "stale",
            "active",
            "heartbeat received",
            previous_last_seen_at,
            now,
            now,
        )
        new_state = "active"
    else:
        # For active or quarantined: only update last_seen_at, do not change state
        await pool.execute(
            "UPDATE butler_registry SET last_seen_at = $1 WHERE name = $2",
            now,
            body.butler_name,
        )
        new_state = current_state

    logger.info(
        "Heartbeat received from butler %r (state: %s → %s)",
        body.butler_name,
        current_state,
        new_state,
    )

    return HeartbeatResponse(status="ok", eligibility_state=new_state)
