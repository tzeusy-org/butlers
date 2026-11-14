"""Home butler endpoints.

Provides endpoints for Home Assistant entity state, areas, command audit log,
snapshot freshness, device inventory, energy consumption, maintenance items,
and threshold configuration. All data is queried directly from the home
butler's PostgreSQL schema via asyncpg.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from starlette.responses import JSONResponse

from butlers.api.audit_emit import authenticated_principal, emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import Db, OptionalDb
from butlers.api.models import (
    ApiResponse,
    ErrorDetail,
    ErrorResponse,
    PaginatedResponse,
    PaginationMeta,
)
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.pagination import Page, decode_cursor, encode_cursor
from butlers.core.audit import append as append_audit
from butlers.core.audit_transaction import audit_transaction
from butlers.core.state import decode_jsonb, state_set
from butlers.credential_store import upsert_owner_entity_info
from butlers.db import register_jsonb_codec
from butlers.home_assistant_client import (
    HAStatisticsClient,
    HAStatisticsError,
    parse_statistics_change_series,
)
from butlers.jobs.home import HASourceUnmeasurableError, _require_ha_source_healthy
from butlers.perception import PerceptionStore
from butlers.roster_imports import roster_module

# Dynamically load models module from the same directory
_models = roster_module("home", "api/models", api_source=__file__)

AreaResponse = _models.AreaResponse
CommandLogEntry = _models.CommandLogEntry
EntityStateResponse = _models.EntityStateResponse
EntitySummaryResponse = _models.EntitySummaryResponse
StatisticsResponse = _models.StatisticsResponse
DeviceInventoryEntry = _models.DeviceInventoryEntry
DeviceInventoryResponse = _models.DeviceInventoryResponse
DevicePaginationMeta = _models.DevicePaginationMeta
EnergyDataPoint = _models.EnergyDataPoint
TopConsumerEntry = _models.TopConsumerEntry
MaintenanceItemResponse = _models.MaintenanceItemResponse
MaintenanceItemCreateRequest = _models.MaintenanceItemCreateRequest
ThresholdConfig = _models.ThresholdConfig
ThresholdUpdateRequest = _models.ThresholdUpdateRequest
BatteryThresholds = _models.BatteryThresholds
OfflineHoursThresholds = _models.OfflineHoursThresholds
ComfortDefaults = _models.ComfortDefaults
ComfortDeviation = _models.ComfortDeviation
EnergyThresholds = _models.EnergyThresholds
AtmosphereCurrentResponse = _models.AtmosphereCurrentResponse
AtmosphereLocationUpdateRequest = _models.AtmosphereLocationUpdateRequest
SafetyWatchEntry = _models.SafetyWatchEntry
SafetyWatchUpsertRequest = _models.SafetyWatchUpsertRequest
DepartureWatchEntry = _models.DepartureWatchEntry
DepartureWatchUpsertRequest = _models.DepartureWatchUpsertRequest
OwnerPlaceInput = _models.OwnerPlaceInput
OwnerPlaceUpdate = _models.OwnerPlaceUpdate
OwnerPlaceResponse = _models.OwnerPlaceResponse
PresenceOwnerEntitiesUpdate = _models.PresenceOwnerEntitiesUpdate
PresenceOwnerEntitiesResponse = _models.PresenceOwnerEntitiesResponse
PresenceTransition = _models.PresenceTransition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/home", tags=["home"])

BUTLER_DB = "home"

# Threshold state store keys
_THRESHOLD_BATTERY_KEY = "home:thresholds:battery"
_THRESHOLD_OFFLINE_KEY = "home:thresholds:offline_hours"
_THRESHOLD_COMFORT_DEFAULTS_KEY = "home:thresholds:comfort_defaults"
_THRESHOLD_COMFORT_DEVIATION_KEY = "home:thresholds:comfort_deviation"
_THRESHOLD_ENERGY_KEY = "home:thresholds:energy"

_ENERGY_UNAVAILABLE_DETAIL = (
    "Energy statistics unavailable: discovered sensors do not provide "
    "finite cumulative-energy change data"
)
_HA_SOURCE_UNAVAILABLE_DETAIL = (
    "Home Assistant is unavailable; current snapshot state cannot be confirmed"
)
_ENERGY_DATA_STATUS_HEADER = "X-Butlers-Energy-Data-Status"
_ENERGY_OMITTED_SENSORS_HEADER = "X-Butlers-Omitted-Sensors"


def _pool(db: DatabaseManager):
    """Retrieve the home butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Home butler database is not available",
        ) from exc


async def _ha_source_available(pool) -> bool:
    """Return whether ``ha_entity_snapshot`` reads can currently be trusted.

    Reuses the home job guard (``_require_ha_source_healthy``,
    bu-8cdl1.12 slice 1) but degrades to a boolean instead of raising: unlike
    a scheduled job, a dashboard read still returns its (possibly stale)
    snapshot rows so the page has something to show, tagged with this flag so
    the frontend can render a degraded-source indicator per the fleet-wide
    "never render an unreachable source as a truthful empty/healthy result"
    convention (docs/api_and_protocols/response-conventions.md) rather than
    silently treating an HA outage as "no devices, no alerts".
    """
    try:
        await _require_ha_source_healthy(pool)
    except HASourceUnmeasurableError:
        return False
    return True


# ---------------------------------------------------------------------------
# GET /api/home/entities — list entities with optional domain/area filters
# ---------------------------------------------------------------------------


@router.get("/entities", response_model=PaginatedResponse[EntitySummaryResponse])
async def list_entities(
    domain: str | None = Query(None, description="Filter by HA domain (e.g. 'light', 'switch')"),
    area: str | None = Query(None, description="Filter by area_id stored in entity attributes"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    *,
    db: Db,
) -> PaginatedResponse[EntitySummaryResponse]:
    """List Home Assistant entities from the snapshot cache.

    Supports optional filtering by domain (derived from entity_id prefix)
    and area (derived from ``attributes->>'area_id'``).
    """
    pool = _pool(db)
    ha_available = await _ha_source_available(pool)

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
            friendly_name=(r["attributes"] or {}).get("friendly_name"),
            domain=r["entity_id"].split(".")[0] if "." in r["entity_id"] else r["entity_id"],
            last_updated=str(r["last_updated"]) if r["last_updated"] else None,
            captured_at=str(r["captured_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[EntitySummaryResponse](
        data=data,
        meta=PaginationMeta(
            total=total, offset=offset, limit=limit, ha_source_available=ha_available
        ),
    )


# ---------------------------------------------------------------------------
# GET /api/home/entities/{entity_id} — single entity detail
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id:path}", response_model=EntityStateResponse)
async def get_entity(
    entity_id: str,
    *,
    db: Db,
) -> EntityStateResponse:
    """Retrieve full state detail for a single Home Assistant entity.

    Returns 404 if the entity is not in the snapshot cache.
    """
    pool = _pool(db)
    ha_available = await _ha_source_available(pool)

    row = await pool.fetchrow(
        "SELECT entity_id, state, attributes, last_updated, captured_at"
        " FROM ha_entity_snapshot"
        " WHERE entity_id = $1",
        entity_id,
    )

    if row is None:
        if not ha_available:
            raise HTTPException(status_code=503, detail=_HA_SOURCE_UNAVAILABLE_DETAIL)
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")

    return EntityStateResponse(
        entity_id=row["entity_id"],
        state=row["state"],
        attributes=dict(row["attributes"] or {}),
        last_updated=str(row["last_updated"]) if row["last_updated"] else None,
        captured_at=str(row["captured_at"]),
        ha_source_available=ha_available,
    )


# ---------------------------------------------------------------------------
# GET /api/home/areas — list areas derived from entity snapshot attributes
# ---------------------------------------------------------------------------


@router.get("/areas", response_model=list[AreaResponse])
async def list_areas(
    *,
    db: Db,
) -> list[AreaResponse]:
    """List all areas found in the Home Assistant entity snapshot cache.

    Areas are derived from the ``area_id`` field in entity attributes JSONB.
    Only entities with a non-null ``area_id`` are included.
    """
    pool = _pool(db)
    ha_available = await _ha_source_available(pool)

    rows = await pool.fetch(
        "SELECT attributes->>'area_id' AS area_id, count(*) AS entity_count"
        " FROM ha_entity_snapshot"
        " WHERE attributes->>'area_id' IS NOT NULL"
        " GROUP BY attributes->>'area_id'"
        " ORDER BY attributes->>'area_id'",
    )

    if not rows and not ha_available:
        raise HTTPException(status_code=503, detail=_HA_SOURCE_UNAVAILABLE_DETAIL)

    return [
        AreaResponse(
            area_id=r["area_id"],
            entity_count=int(r["entity_count"]),
            ha_source_available=ha_available,
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
    *,
    db: Db,
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
        f"SELECT id, domain, service, target, data, result, context_id, issued_at, "
        f"attempt_id, risk, actor, session_id, approval_id, requested_state, "
        f"observed_state, status, rollback_hint, failure_reason, completed_at"
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
            result=(
                dict(r["result"])
                if isinstance(r["result"], dict)
                else {"value": r["result"]}
                if r["result"] is not None
                else None
            ),
            context_id=r["context_id"],
            issued_at=str(r["issued_at"]),
            attempt_id=r.get("attempt_id"),
            risk=r.get("risk"),
            actor=r.get("actor"),
            session_id=r.get("session_id"),
            approval_id=r.get("approval_id"),
            requested_state=(dict(r.get("requested_state")) if r.get("requested_state") else None),
            observed_state=(dict(r.get("observed_state")) if r.get("observed_state") else None),
            status=r.get("status"),
            rollback_hint=(dict(r.get("rollback_hint")) if r.get("rollback_hint") else None),
            failure_reason=r.get("failure_reason"),
            completed_at=str(r.get("completed_at")) if r.get("completed_at") else None,
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
    *,
    db: Db,
) -> StatisticsResponse:
    """Return entity snapshot freshness and aggregate statistics.

    Reports total entity count, per-domain counts, and the oldest/newest
    ``captured_at`` timestamps in the snapshot cache.
    """
    pool = _pool(db)
    ha_available = await _ha_source_available(pool)

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
        ha_source_available=ha_available,
    )


# ---------------------------------------------------------------------------
# GET /api/home/devices — device inventory with domain/area/health filters
# ---------------------------------------------------------------------------


def _compute_health_status(state: str | None) -> str:
    """Compute health_status from HA entity state."""
    if state in ("unavailable", "unknown"):
        return "offline"
    return "healthy"


@router.get("/devices", response_model=DeviceInventoryResponse)
async def list_devices(
    domain: str | None = Query(None, description="Filter by HA domain (e.g. 'light', 'switch')"),
    area: str | None = Query(None, description="Filter by area name from entity attributes"),
    health: str | None = Query(None, description="Filter by health status: 'healthy' or 'offline'"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    *,
    db: Db,
) -> DeviceInventoryResponse:
    """List all known HA devices with current state, area, and health status.

    Supports optional filtering by domain, area name, and health status.
    Uses page-based pagination.
    """
    pool = _pool(db)
    ha_available = await _ha_source_available(pool)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if domain is not None:
        conditions.append(f"entity_id LIKE ${idx} || '.%%'")
        args.append(domain)
        idx += 1

    if area is not None:
        conditions.append(f"(attributes->>'area_name' = ${idx} OR attributes->>'area_id' = ${idx})")
        args.append(area)
        idx += 1

    if health == "offline":
        conditions.append("state IN ('unavailable', 'unknown')")
    elif health == "healthy":
        conditions.append("state NOT IN ('unavailable', 'unknown')")

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total_count: int = (
        await pool.fetchval(f"SELECT count(*) FROM ha_entity_snapshot{where}", *args) or 0
    )

    offset = (page - 1) * page_size
    rows = await pool.fetch(
        f"SELECT entity_id, state, attributes, last_updated"
        f" FROM ha_entity_snapshot{where}"
        f" ORDER BY entity_id"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        page_size,
    )

    data = []
    for r in rows:
        attrs: dict[str, Any] = dict(r["attributes"] or {})
        entity_id: str = r["entity_id"]
        state: str = r["state"] or ""
        domain_val = entity_id.split(".")[0] if "." in entity_id else entity_id
        area_name = attrs.get("area_name") or attrs.get("area_id")
        last_updated_raw = r["last_updated"]
        last_updated: datetime | None = None
        if last_updated_raw is not None:
            if isinstance(last_updated_raw, datetime):
                last_updated = last_updated_raw
            else:
                try:
                    last_updated = datetime.fromisoformat(str(last_updated_raw))
                except ValueError:
                    pass

        data.append(
            DeviceInventoryEntry(
                entity_id=entity_id,
                state=state,
                friendly_name=attrs.get("friendly_name"),
                area_name=area_name,
                domain=domain_val,
                last_updated=last_updated,
                health_status=_compute_health_status(state),
            )
        )

    meta = DevicePaginationMeta(
        page=page,
        page_size=page_size,
        total_count=total_count,
        ha_source_available=ha_available,
    )
    return DeviceInventoryResponse(data=data, meta=meta)


# ---------------------------------------------------------------------------
# GET /api/home/energy — energy consumption time series
# ---------------------------------------------------------------------------


async def _get_ha_credentials(pool) -> tuple[str | None, str | None]:
    """Read HA URL and token from the butler state store."""
    try:
        rows = await pool.fetch("SELECT key, value FROM state WHERE key IN ('ha_url', 'ha_token')")
        kv: dict[str, Any] = {}
        for row in rows:
            val = row["value"]
            if isinstance(val, str):
                kv[row["key"]] = val
            elif isinstance(val, dict):
                kv[row["key"]] = val
        ha_url = kv.get("ha_url")
        ha_token = kv.get("ha_token")
        if isinstance(ha_url, dict):
            ha_url = ha_url.get("value")
        if isinstance(ha_token, dict):
            ha_token = ha_token.get("value")
        return ha_url, ha_token
    except Exception:
        return None, None


def _statistics_timestamp(value: object) -> datetime | None:
    """Normalize an HA statistics timestamp to an aware datetime."""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _partition_energy_statistics(
    statistic_ids: list[str],
    ha_data: dict[str, Any],
) -> tuple[dict[str, tuple[list[dict[str, Any]], list[float]]], list[str]]:
    """Separate supported cumulative-energy series from unsupported sensors."""
    supported: dict[str, tuple[list[dict[str, Any]], list[float]]] = {}
    unsupported: list[str] = []
    for entity_id in statistic_ids:
        series = ha_data.get(entity_id)
        changes = parse_statistics_change_series(series)
        if changes is None:
            unsupported.append(entity_id)
            continue
        supported[entity_id] = (series, changes)
    return supported, unsupported


def _set_partial_energy_headers(response: Response, omitted_count: int) -> None:
    if omitted_count:
        response.headers[_ENERGY_DATA_STATUS_HEADER] = "partial"
        response.headers[_ENERGY_OMITTED_SENSORS_HEADER] = str(omitted_count)


@router.get("/energy")
async def get_energy(
    response: Response,
    period: str = Query("day", description="Aggregation period: 'day' or 'hour'"),
    start: str | None = Query(None, description="Start date (ISO 8601). Default: 7 days ago."),
    end: str | None = Query(None, description="End date (ISO 8601). Default: now."),
    *,
    db: Db,
) -> list[dict[str, Any]]:
    """Return energy consumption time-series data.

    Queries HA's ``recorder/statistics_during_period`` WebSocket command.
    Returns 503 if Home Assistant is unavailable.
    """
    pool = _pool(db)
    if period not in {"day", "hour"}:
        raise HTTPException(status_code=422, detail="Period must be 'day' or 'hour'")

    # Default date range
    now = datetime.now(UTC)
    end_dt = now
    start_dt = now - timedelta(days=7)

    if start is not None:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid 'start' datetime format. Expected ISO 8601.",
            ) from exc
    if end is not None:
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid 'end' datetime format. Expected ISO 8601.",
            ) from exc

    if not await _ha_source_available(pool):
        raise HTTPException(status_code=503, detail=_HA_SOURCE_UNAVAILABLE_DETAIL)

    ha_url, ha_token = await _get_ha_credentials(pool)
    if not ha_url or not ha_token:
        raise HTTPException(
            status_code=503,
            detail="Home Assistant is unavailable: HA URL or token not configured",
        )

    # Discover energy sensors from snapshot
    try:
        sensor_rows = await pool.fetch(
            "SELECT entity_id, attributes->>'friendly_name' AS friendly_name"
            " FROM ha_entity_snapshot"
            " WHERE entity_id LIKE 'sensor.%'"
            "   AND (entity_id ILIKE '%energy%'"
            "     OR entity_id ILIKE '%kwh%'"
            "     OR entity_id ILIKE '%power%'"
            "     OR entity_id ILIKE '%consumption%'"
            "     OR entity_id ILIKE '%watt%')"
        )
        statistic_ids = [r["entity_id"] for r in sensor_rows]
    except Exception as exc:
        logger.warning("Failed to query energy sensors from snapshot: %s", exc)
        statistic_ids = []

    if not statistic_ids:
        return []

    statistics_client = HAStatisticsClient(ha_url=ha_url, ha_token=ha_token)
    try:
        ha_data = await statistics_client.get_statistics(
            statistic_ids=statistic_ids,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            period=period,
            types=("change",),
        )
    except HAStatisticsError as exc:
        logger.warning("HA energy statistics unavailable: code=%s", exc.code)
        raise HTTPException(
            status_code=503,
            detail="Home Assistant is unavailable",
        ) from None

    supported, unsupported = _partition_energy_statistics(statistic_ids, ha_data)
    if not supported:
        raise HTTPException(status_code=503, detail=_ENERGY_UNAVAILABLE_DETAIL)
    _set_partial_energy_headers(response, len(unsupported))

    # Aggregate by timestamp bucket
    buckets: dict[datetime, dict[str, float]] = {}
    for entity_id, (stat_list, changes) in supported.items():
        for stat, kwh in zip(stat_list, changes, strict=True):
            ts = stat.get("start") or stat.get("end")
            ts_dt = _statistics_timestamp(ts)
            if ts_dt is None:
                continue
            if ts_dt not in buckets:
                buckets[ts_dt] = {}
            buckets[ts_dt][entity_id] = buckets[ts_dt].get(entity_id, 0) + kwh

    result = []
    for ts_dt in sorted(buckets):
        device_map = buckets[ts_dt]
        total_kwh = sum(device_map.values())
        result.append(
            EnergyDataPoint(
                timestamp=ts_dt,
                total_kwh=total_kwh,
                devices=device_map,
            ).model_dump(mode="json")
        )

    return result


# ---------------------------------------------------------------------------
# GET /api/home/energy/top-consumers
# ---------------------------------------------------------------------------


@router.get("/energy/top-consumers")
async def get_energy_top_consumers(
    response: Response,
    start: str | None = Query(None, description="Start date (ISO 8601). Default: 7 days ago."),
    end: str | None = Query(None, description="End date (ISO 8601). Default: now."),
    *,
    db: Db,
) -> list[dict[str, Any]]:
    """Return the top 10 energy-consuming devices for the given period.

    Each entry includes entity_id, friendly_name, total_kwh, and percentage
    of total consumption. Returns 503 if Home Assistant is unavailable.
    """
    pool = _pool(db)

    now = datetime.now(UTC)
    end_dt = now
    start_dt = now - timedelta(days=7)

    if start is not None:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid ISO 8601 datetime for 'start'",
            ) from exc
    if end is not None:
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid ISO 8601 datetime for 'end'",
            ) from exc

    if not await _ha_source_available(pool):
        raise HTTPException(status_code=503, detail=_HA_SOURCE_UNAVAILABLE_DETAIL)

    ha_url, ha_token = await _get_ha_credentials(pool)
    if not ha_url or not ha_token:
        raise HTTPException(
            status_code=503,
            detail="Home Assistant is unavailable: HA URL or token not configured",
        )

    # Discover energy sensors
    try:
        sensor_rows = await pool.fetch(
            "SELECT entity_id, attributes->>'friendly_name' AS friendly_name"
            " FROM ha_entity_snapshot"
            " WHERE entity_id LIKE 'sensor.%'"
            "   AND (entity_id ILIKE '%energy%'"
            "     OR entity_id ILIKE '%kwh%'"
            "     OR entity_id ILIKE '%power%'"
            "     OR entity_id ILIKE '%consumption%'"
            "     OR entity_id ILIKE '%watt%')"
        )
        statistic_ids = [r["entity_id"] for r in sensor_rows]
        friendly_names = {r["entity_id"]: r["friendly_name"] for r in sensor_rows}
    except Exception as exc:
        logger.warning("Failed to query energy sensors: %s", exc)
        statistic_ids = []
        friendly_names = {}

    if not statistic_ids:
        return []

    statistics_client = HAStatisticsClient(ha_url=ha_url, ha_token=ha_token)
    try:
        ha_data = await statistics_client.get_statistics(
            statistic_ids=statistic_ids,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            period="day",
            types=("change",),
        )
    except HAStatisticsError as exc:
        logger.warning("HA top-consumer statistics unavailable: code=%s", exc.code)
        raise HTTPException(
            status_code=503,
            detail="Home Assistant is unavailable",
        ) from None

    supported, unsupported = _partition_energy_statistics(statistic_ids, ha_data)
    if not supported:
        raise HTTPException(status_code=503, detail=_ENERGY_UNAVAILABLE_DETAIL)
    _set_partial_energy_headers(response, len(unsupported))

    # Sum per device
    device_totals = {entity_id: sum(changes) for entity_id, (_series, changes) in supported.items()}

    grand_total = sum(device_totals.values())

    # Top 10 by consumption
    sorted_devices = sorted(device_totals.items(), key=lambda x: x[1], reverse=True)[:10]

    result = []
    for entity_id, total_kwh in sorted_devices:
        pct = (total_kwh / grand_total * 100) if grand_total > 0 else 0.0
        result.append(
            TopConsumerEntry(
                entity_id=entity_id,
                friendly_name=friendly_names.get(entity_id),
                total_kwh=total_kwh,
                percentage=round(pct, 2),
            ).model_dump(mode="json")
        )

    return result


# ---------------------------------------------------------------------------
# Maintenance helpers
# ---------------------------------------------------------------------------


def _compute_maintenance_status(
    next_due_at: datetime | None,
    last_completed_at: datetime | None,
    now: datetime | None = None,
) -> str:
    """Compute maintenance item status from timestamps."""
    if now is None:
        now = datetime.now(UTC)
    if next_due_at is None:
        return "ok"
    # Make next_due_at timezone-aware if naive
    if next_due_at.tzinfo is None:
        next_due_at = next_due_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    delta = (now - next_due_at).total_seconds()
    if delta > 0:
        return "overdue"
    days_until = -delta / 86400
    if days_until <= 7:
        return "due"
    if days_until <= 30:
        return "upcoming"
    return "ok"


def _row_to_maintenance_item(row: Any) -> MaintenanceItemResponse:
    """Convert an asyncpg row to a MaintenanceItemResponse."""
    next_due_at = row["next_due_at"]
    last_completed_at = row["last_completed_at"]

    # Normalize to datetime objects
    def _to_dt(val: Any) -> datetime | None:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val))
        except ValueError:
            return None

    next_due_dt = _to_dt(next_due_at)
    last_completed_dt = _to_dt(last_completed_at)

    status = _compute_maintenance_status(next_due_dt, last_completed_dt)

    return MaintenanceItemResponse(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        interval_days=row["interval_days"],
        last_completed_at=last_completed_dt,
        next_due_at=next_due_dt,
        status=status,
        notes=row["notes"],
    )


# ---------------------------------------------------------------------------
# GET /api/home/maintenance — list maintenance items
# ---------------------------------------------------------------------------


@router.get("/maintenance")
async def list_maintenance(
    category: str | None = Query(None, description="Filter by category"),
    status: str | None = Query(
        None, description="Filter by computed status: overdue, due, upcoming, ok"
    ),
    *,
    db: Db,
) -> list[dict[str, Any]]:
    """List all maintenance items sorted by next_due_at ascending (NULLs first).

    Optionally filter by category or computed status.
    """
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if category is not None:
        conditions.append(f"category = ${idx}")
        args.append(category)
        idx += 1

    # status filtering is applied post-query (computed field)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # SQL-level status pre-filter for common cases
    if status == "overdue":
        sep = " AND " if conditions else " WHERE "
        where += f"{sep}next_due_at < now()"
    elif status == "due":
        # next_due_at within next 0 days (overdue) to 0+ (basically overdue)
        # The spec says "status=overdue → next_due_at < now()"
        # "status=due" isn't explicitly SQL-filtered; handled post-query
        pass

    try:
        rows = await pool.fetch(
            f"SELECT id, name, category, interval_days, last_completed_at, next_due_at, notes"
            f" FROM maintenance_items{where}"
            f" ORDER BY next_due_at ASC NULLS FIRST"
        )
    except Exception as exc:
        err_str = str(exc)
        if "does not exist" in err_str or "relation" in err_str:
            raise HTTPException(
                status_code=503,
                detail="Maintenance items table not found. Run migrations first.",
            ) from exc
        raise

    items = [_row_to_maintenance_item(r) for r in rows]

    # Apply post-query status filter if needed
    if status is not None and status != "overdue":
        items = [i for i in items if i.status == status]

    return [i.model_dump(mode="json") for i in items]


# ---------------------------------------------------------------------------
# POST /api/home/maintenance — create maintenance item
# ---------------------------------------------------------------------------


@router.post("/maintenance", status_code=201)
async def create_maintenance_item(
    request: Request,
    body: MaintenanceItemCreateRequest,
    *,
    db: Db,
) -> dict[str, Any]:
    """Create a new maintenance item.

    Returns HTTP 201 with the created item.
    """
    pool = _pool(db)

    try:
        row = await pool.fetchrow(
            "INSERT INTO maintenance_items (name, category, interval_days, notes)"
            " VALUES ($1, $2, $3, $4)"
            " RETURNING id, name, category, interval_days,"
            "   last_completed_at, next_due_at, notes",
            body.name,
            body.category,
            body.interval_days,
            body.notes,
        )
    except Exception as exc:
        err_str = str(exc)
        if "unique" in err_str.lower() or "duplicate" in err_str.lower():
            raise HTTPException(
                status_code=409,
                detail=f"Maintenance item with name '{body.name}' already exists",
            ) from exc
        if "does not exist" in err_str or "relation" in err_str:
            raise HTTPException(
                status_code=503,
                detail="Maintenance items table not found. Run migrations first.",
            ) from exc
        raise

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create maintenance item")

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="home",
        operation="maintenance_item_create",
        method="POST",
        path="/api/home/maintenance",
        body={"name": body.name, "category": body.category},
        response_status=201,
        request=request,
    )

    return _row_to_maintenance_item(row).model_dump(mode="json")


# ---------------------------------------------------------------------------
# POST /api/home/maintenance/{item_id}/complete — complete a maintenance item
# ---------------------------------------------------------------------------


@router.post("/maintenance/{item_id}/complete")
async def complete_maintenance_item(
    item_id: UUID,
    request: Request,
    *,
    db: Db,
) -> dict[str, Any]:
    """Mark a maintenance item as completed.

    Sets last_completed_at to now and recomputes next_due_at.
    Returns 404 if item does not exist.
    """
    pool = _pool(db)

    try:
        row = await pool.fetchrow(
            "UPDATE maintenance_items"
            " SET last_completed_at = now(),"
            "     next_due_at = now() + interval_days * INTERVAL '1 day',"
            "     updated_at = now()"
            " WHERE id = $1"
            " RETURNING id, name, category, interval_days,"
            "   last_completed_at, next_due_at, notes",
            item_id,
        )
    except Exception as exc:
        err_str = str(exc)
        if "does not exist" in err_str or "relation" in err_str:
            raise HTTPException(
                status_code=503,
                detail="Maintenance items table not found. Run migrations first.",
            ) from exc
        raise

    if row is None:
        raise HTTPException(status_code=404, detail=f"Maintenance item not found: {item_id}")

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="home",
        operation="maintenance_item_complete",
        method="POST",
        path=f"/api/home/maintenance/{item_id}/complete",
        path_params={"item_id": str(item_id)},
        response_status=200,
        request=request,
    )

    return _row_to_maintenance_item(row).model_dump(mode="json")


# ---------------------------------------------------------------------------
# DELETE /api/home/maintenance/{item_id} — delete maintenance item
# ---------------------------------------------------------------------------


@router.delete("/maintenance/{item_id}", status_code=204)
async def delete_maintenance_item(
    item_id: UUID,
    request: Request,
    *,
    db: Db,
) -> None:
    """Delete a maintenance item.

    Returns 204 on success, 404 if item does not exist.
    """
    pool = _pool(db)

    try:
        result = await pool.fetchval(
            "DELETE FROM maintenance_items WHERE id = $1 RETURNING id",
            item_id,
        )
    except Exception as exc:
        err_str = str(exc)
        if "does not exist" in err_str or "relation" in err_str:
            raise HTTPException(
                status_code=503,
                detail="Maintenance items table not found. Run migrations first.",
            ) from exc
        raise

    if result is None:
        raise HTTPException(status_code=404, detail=f"Maintenance item not found: {item_id}")

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="home",
        operation="maintenance_item_delete",
        method="DELETE",
        path=f"/api/home/maintenance/{item_id}",
        path_params={"item_id": str(item_id)},
        response_status=204,
        request=request,
    )


# ---------------------------------------------------------------------------
# Threshold helpers
# ---------------------------------------------------------------------------


_DEFAULT_THRESHOLDS = {
    "battery": {"critical": 10, "warning": 20, "info": 30},
    "offline_hours": {"critical": 24, "warning": 1},
    "comfort_defaults": {
        "temp_min_c": 20,
        "temp_max_c": 27.5,
        "humidity_min": 30,
        "humidity_max": 78,
        "co2_max_ppm": 1000,
    },
    "comfort_deviation": {
        "minor_temp_c": 1,
        "moderate_temp_c": 3,
        "minor_humidity": 10,
        "moderate_humidity": 20,
        "critical_temp_low_c": 15.5,
        "critical_temp_high_c": 32,
        "critical_co2_ppm": 1500,
        "critical_humidity_low": 15,
        "critical_humidity_high": 88,
    },
    "energy": {"anomaly_pct": 20, "high_severity_pct": 100},
}

_THRESHOLD_KEYS = {
    "battery": _THRESHOLD_BATTERY_KEY,
    "offline_hours": _THRESHOLD_OFFLINE_KEY,
    "comfort_defaults": _THRESHOLD_COMFORT_DEFAULTS_KEY,
    "comfort_deviation": _THRESHOLD_COMFORT_DEVIATION_KEY,
    "energy": _THRESHOLD_ENERGY_KEY,
}


async def _load_thresholds(pool) -> dict[str, Any]:
    """Load all threshold values from the state store, falling back to defaults."""
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM state WHERE key = ANY($1::text[])",
            list(_THRESHOLD_KEYS.values()),
        )
        stored: dict[str, Any] = {}
        for row in rows:
            # Map state key back to threshold name
            for name, store_key in _THRESHOLD_KEYS.items():
                if row["key"] == store_key:
                    val = row["value"]
                    if isinstance(val, str):
                        try:
                            val = json.loads(val)
                        except Exception:
                            pass
                    stored[name] = val
    except Exception:
        stored = {}

    result: dict[str, Any] = {}
    for name, default in _DEFAULT_THRESHOLDS.items():
        result[name] = stored.get(name, default)
    return result


# ---------------------------------------------------------------------------
# GET /api/home/settings/thresholds
# ---------------------------------------------------------------------------


@router.get("/settings/thresholds")
async def get_thresholds(
    *,
    db: Db,
) -> dict[str, Any]:
    """Return the current threshold configuration for all home monitoring jobs.

    Values are read from the state store (``home:thresholds:*`` keys).
    Falls back to defaults when a key is not set.
    """
    pool = _pool(db)
    thresholds = await _load_thresholds(pool)
    return thresholds


# ---------------------------------------------------------------------------
# PATCH /api/home/settings/thresholds
# ---------------------------------------------------------------------------


@router.patch("/settings/thresholds")
async def update_thresholds(
    request: Request,
    body: ThresholdUpdateRequest,
    *,
    db: Db,
) -> dict[str, Any]:
    """Partially update threshold configuration.

    Only provided fields are merged into the stored values. Each threshold
    group is stored individually under its ``home:thresholds:*`` key.
    Returns the updated full threshold configuration.
    """
    pool = _pool(db)

    # Load current values
    current = await _load_thresholds(pool)

    # Deep-merge updates: for each group provided, merge only the explicitly set
    # subfields into the current stored values, preserving unset subfields.
    update_map: dict[str, Any] = {}
    if body.battery is not None:
        merged = {**current.get("battery", {}), **body.battery.model_dump(exclude_unset=True)}
        _validate_battery_thresholds(merged)
        update_map["battery"] = merged
    if body.offline_hours is not None:
        update_map["offline_hours"] = {
            **current.get("offline_hours", {}),
            **body.offline_hours.model_dump(exclude_unset=True),
        }
    if body.comfort_defaults is not None:
        update_map["comfort_defaults"] = {
            **current.get("comfort_defaults", {}),
            **body.comfort_defaults.model_dump(exclude_unset=True),
        }
    if body.comfort_deviation is not None:
        update_map["comfort_deviation"] = {
            **current.get("comfort_deviation", {}),
            **body.comfort_deviation.model_dump(exclude_unset=True),
        }
    if body.energy is not None:
        merged = {**current.get("energy", {}), **body.energy.model_dump(exclude_unset=True)}
        _validate_energy_thresholds(merged)
        update_map["energy"] = merged

    # Persist each updated key to the state store
    for name, value in update_map.items():
        store_key = _THRESHOLD_KEYS[name]
        try:
            await pool.execute(
                "INSERT INTO state (key, value, updated_at)"
                " VALUES ($1, $2::jsonb, now())"
                " ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, updated_at = now()",
                store_key,
                value,
            )
        except Exception as exc:
            logger.warning("Failed to persist threshold key %s: %s", store_key, exc)
            raise HTTPException(
                status_code=503,
                detail=f"Failed to persist threshold: {store_key}",
            ) from exc

    # Merge into current and return
    current.update(update_map)

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="home",
        operation="threshold_settings_patch",
        method="PATCH",
        path="/api/home/settings/thresholds",
        body={"updated_keys": list(update_map.keys())},
        response_status=200,
        request=request,
    )

    return current


def _validate_battery_thresholds(data: dict[str, Any]) -> None:
    """Validate battery threshold numeric constraints."""
    critical = data.get("critical", 0)
    warning = data.get("warning", 0)
    info = data.get("info", 0)
    if not (0 <= critical <= warning <= info <= 100):
        raise HTTPException(
            status_code=422,
            detail="Battery thresholds must satisfy: 0 <= critical <= warning <= info <= 100",
        )


def _validate_energy_thresholds(data: dict[str, Any]) -> None:
    """Validate energy threshold numeric constraints."""
    anomaly_pct = data.get("anomaly_pct", 0)
    high_severity_pct = data.get("high_severity_pct", 0)
    if anomaly_pct < 0 or high_severity_pct < 0:
        raise HTTPException(
            status_code=422,
            detail="Energy thresholds must be non-negative percentages",
        )
    if anomaly_pct > high_severity_pct:
        raise HTTPException(
            status_code=422,
            detail="anomaly_pct must be <= high_severity_pct",
        )


# ---------------------------------------------------------------------------
# GET /api/home/atmosphere/current — shared weather/AQI/pollen context feed
# ---------------------------------------------------------------------------

# A reading older than this many poll cycles (job cadence: every 30 min) is
# stale — the owner should see a degraded flag rather than trust a cached
# value indefinitely.
_ATMOSPHERE_STALE_THRESHOLD = timedelta(hours=2)


@router.get("/atmosphere/current", response_model=AtmosphereCurrentResponse)
async def get_atmosphere_current(
    *,
    db: Db,
) -> AtmosphereCurrentResponse:
    """Return current weather/AQI/pollen conditions for the home location.

    ``configured=False`` when no home location is on file — a legitimate
    absence (the owner has not set one up yet), not an error. When
    configured, ``stale``/``source_error`` surface a degraded feed per the
    CLAUDE.md Degraded-Mode Response Envelope convention rather than
    silently returning a truthful-looking empty/zeroed reading.
    """
    pool = _pool(db)
    status_row = await pool.fetchrow(
        """
        SELECT configured, latitude, longitude, last_success_at, last_error,
               consecutive_failures
        FROM public.atmosphere_feed_status
        WHERE id = 1
        """
    )
    if status_row is None or not status_row["configured"]:
        return AtmosphereCurrentResponse(configured=False)

    reading = await pool.fetchrow(
        """
        SELECT observed_at, temperature_c, apparent_temperature_c, relative_humidity_pct,
               precipitation_mm, weather_code, wind_speed_kph, aqi_us, aqi_european,
               pm2_5, pm10, pollen_tree, pollen_grass, pollen_weed, pollen_available,
               fetched_at
        FROM public.atmosphere_readings
        WHERE latitude = $1 AND longitude = $2
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        status_row["latitude"],
        status_row["longitude"],
    )

    last_success_at = status_row["last_success_at"]
    stale = (
        last_success_at is None
        or (datetime.now(UTC) - last_success_at) > _ATMOSPHERE_STALE_THRESHOLD
    )
    source_error = bool(status_row["last_error"]) and status_row["consecutive_failures"] > 0

    if reading is None:
        return AtmosphereCurrentResponse(
            configured=True,
            latitude=status_row["latitude"],
            longitude=status_row["longitude"],
            stale=True,
            source_error=source_error,
            last_error=status_row["last_error"],
        )

    return AtmosphereCurrentResponse(
        configured=True,
        latitude=status_row["latitude"],
        longitude=status_row["longitude"],
        observed_at=reading["observed_at"].isoformat() if reading["observed_at"] else None,
        temperature_c=reading["temperature_c"],
        apparent_temperature_c=reading["apparent_temperature_c"],
        relative_humidity_pct=reading["relative_humidity_pct"],
        precipitation_mm=reading["precipitation_mm"],
        weather_code=reading["weather_code"],
        wind_speed_kph=reading["wind_speed_kph"],
        aqi_us=reading["aqi_us"],
        aqi_european=reading["aqi_european"],
        pm2_5=reading["pm2_5"],
        pm10=reading["pm10"],
        pollen_tree=reading["pollen_tree"],
        pollen_grass=reading["pollen_grass"],
        pollen_weed=reading["pollen_weed"],
        pollen_available=reading["pollen_available"],
        stale=stale,
        source_error=source_error,
        last_error=status_row["last_error"],
        fetched_at=reading["fetched_at"].isoformat() if reading["fetched_at"] else None,
    )


# ---------------------------------------------------------------------------
# PATCH /api/home/atmosphere/location — owner-provisioned home location
# ---------------------------------------------------------------------------


@router.patch("/atmosphere/location")
async def update_atmosphere_location(
    request: Request,
    body: AtmosphereLocationUpdateRequest,
    *,
    db: Db,
) -> dict[str, Any]:
    """Set the home location used by the atmosphere context feed.

    Stores ``"lat,lon"`` in the owner's ``entity_info`` (type
    ``home_coordinates``, non-secret technical config — mirrors
    ``home_assistant_url``). The next scheduled ``atmosphere_feed_refresh``
    run picks it up; this endpoint does not fetch conditions synchronously.
    """
    pool = _pool(db)
    value = f"{body.latitude},{body.longitude}"
    stored = await upsert_owner_entity_info(pool, "home_coordinates", value, secured=False)
    if not stored:
        raise HTTPException(
            status_code=503,
            detail="No owner entity found — cannot store home location",
        )

    await emit_dashboard_audit(
        db,
        butler="home",
        operation="atmosphere_location_patch",
        method="PATCH",
        path="/api/home/atmosphere/location",
        body={"latitude": body.latitude, "longitude": body.longitude},
        response_status=200,
        request=request,
    )
    return {"latitude": body.latitude, "longitude": body.longitude}


# ---------------------------------------------------------------------------
# /api/home/safety-watch — owner-declared leak/smoke/CO/gas sensors (bu-jv1moa.6)
# ---------------------------------------------------------------------------


def _safety_watch_entry(row: Any) -> SafetyWatchEntry:
    updated_at = row["updated_at"]
    return SafetyWatchEntry(
        entity_id=row["entity_id"],
        role=row["role"],
        owner_label=row["owner_label"],
        updated_at=updated_at.isoformat() if updated_at else None,
    )


@router.get("/safety-watch", response_model=list[SafetyWatchEntry])
async def list_safety_watch(
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: Db,
) -> list[SafetyWatchEntry]:
    """The sensors the safety sentinel watches, as the owner declared them."""
    rows = await _pool(db).fetch(
        "SELECT entity_id, role, owner_label, updated_at FROM safety_watch ORDER BY entity_id, role"
    )
    return [_safety_watch_entry(row) for row in rows]


@router.put("/safety-watch", response_model=SafetyWatchEntry)
async def upsert_safety_watch(
    request: Request,
    body: SafetyWatchUpsertRequest,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: Db,
) -> SafetyWatchEntry:
    """Declare a binary sensor as guarding against a hazard, or relabel it.

    The label is the owner's own wording and is the only name an alert uses; Home Assistant's
    ``friendly_name`` is never read.  Only ``binary_sensor`` entities are accepted.
    """
    row = await _pool(db).fetchrow(
        "INSERT INTO safety_watch (entity_id, role, owner_label) VALUES ($1, $2, $3) "
        "ON CONFLICT (entity_id, role) DO UPDATE "
        "SET owner_label = EXCLUDED.owner_label, updated_at = now() "
        "RETURNING entity_id, role, owner_label, updated_at",
        body.entity_id,
        body.role,
        body.owner_label,
    )
    await emit_dashboard_audit(
        db,
        butler="home",
        operation="safety_watch_upsert",
        method="PUT",
        path="/api/home/safety-watch",
        body={"entity_id": body.entity_id, "role": body.role},
        response_status=200,
        request=request,
    )
    return _safety_watch_entry(row)


@router.delete("/safety-watch/{entity_id}/{role}", status_code=204)
async def delete_safety_watch(
    request: Request,
    entity_id: str,
    role: str,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: Db,
) -> Response:
    """Stop watching a sensor for one hazard; an open condition resolves on the next sweep."""
    status = await _pool(db).execute(
        "DELETE FROM safety_watch WHERE entity_id = $1 AND role = $2", entity_id, role
    )
    if status == "DELETE 0":
        raise HTTPException(status_code=404, detail="No such safety watch")
    await emit_dashboard_audit(
        db,
        butler="home",
        operation="safety_watch_delete",
        method="DELETE",
        path="/api/home/safety-watch",
        body={"entity_id": entity_id, "role": role},
        response_status=204,
        request=request,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# /api/home/departure-watch — what the departure card checks (bu-jv1moa.8)
# ---------------------------------------------------------------------------


def _departure_watch_entry(row: Any) -> DepartureWatchEntry:
    updated_at = row["updated_at"]
    return DepartureWatchEntry(
        entity_id=row["entity_id"],
        owner_label=row["owner_label"],
        secure_states=list(row["secure_states"]),
        updated_at=updated_at.isoformat() if updated_at else None,
    )


@router.get("/departure-watch", response_model=list[DepartureWatchEntry])
async def list_departure_watch(
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: Db,
) -> list[DepartureWatchEntry]:
    """The entities the departure card checks, as the owner declared them."""
    rows = await _pool(db).fetch(
        "SELECT entity_id, owner_label, secure_states, updated_at FROM departure_watch "
        "ORDER BY entity_id"
    )
    return [_departure_watch_entry(row) for row in rows]


@router.put("/departure-watch", response_model=DepartureWatchEntry)
async def upsert_departure_watch(
    request: Request,
    body: DepartureWatchUpsertRequest,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: Db,
) -> DepartureWatchEntry:
    """Declare an entity for the departure card, or change its label or secure states.

    The label is the owner's own wording and is the only name a card uses; Home Assistant's
    ``friendly_name`` is never read.  A state outside ``secure_states`` is listed on the card.
    """
    row = await _pool(db).fetchrow(
        "INSERT INTO departure_watch (entity_id, owner_label, secure_states) VALUES ($1, $2, $3) "
        "ON CONFLICT (entity_id) DO UPDATE SET owner_label = EXCLUDED.owner_label, "
        "secure_states = EXCLUDED.secure_states, updated_at = now() "
        "RETURNING entity_id, owner_label, secure_states, updated_at",
        body.entity_id,
        body.owner_label,
        body.secure_states,
    )
    await emit_dashboard_audit(
        db,
        butler="home",
        operation="departure_watch_upsert",
        method="PUT",
        path="/api/home/departure-watch",
        body={"entity_id": body.entity_id},
        response_status=200,
        request=request,
    )
    return _departure_watch_entry(row)


@router.delete("/departure-watch/{entity_id}", status_code=204)
async def delete_departure_watch(
    request: Request,
    entity_id: str,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: Db,
) -> Response:
    """Stop checking an entity on departure."""
    status = await _pool(db).execute("DELETE FROM departure_watch WHERE entity_id = $1", entity_id)
    if status == "DELETE 0":
        raise HTTPException(status_code=404, detail="No such departure watch")
    await emit_dashboard_audit(
        db,
        butler="home",
        operation="departure_watch_delete",
        method="DELETE",
        path="/api/home/departure-watch",
        body={"entity_id": entity_id},
        response_status=204,
        request=request,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# /api/home/places — the owner place registry editor (public.owner_places; bu-jv1moa.7)
# ---------------------------------------------------------------------------
#
# Owner-only and content-blind.  Owner control is a dependency, and the handlers read the
# body themselves, so a denied request is refused before any body octet, decode or pool
# access.  DashboardAuditMiddleware skips this prefix by path before reading the body; each
# write records one explicit allowlisted audit row inside its own transaction instead.  No
# response, log line or audit row echoes a coordinate, SSID, alias, label or zone id.

_PLACES_LOCK = "butlers:home:owner-places:v1"
_PLACES_MAX_BODY_BYTES = 16_384
_PLACE_COLUMNS = (
    "id, label, kind, lat, lon, radius_m, aliases, ssids, ha_zone, paused, version, updated_at"
)


class _PlaceRefusal(Exception):
    """A fixed, content-blind refusal for the owner place routes."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


_INVALID_PLACE = (422, "INVALID_REQUEST", "Place request is invalid.")
_PLACES_UNAVAILABLE = (503, "PLACES_UNAVAILABLE", "Place registry is unavailable.")
_PLACE_NOT_FOUND = (404, "PLACE_NOT_FOUND", "Place not found.")
_PLACE_VERSION_CONFLICT = (
    409,
    "VERSION_CONFLICT",
    "This place changed since it was loaded. Reload and try again.",
)
_PLACE_CONFLICT = (
    409,
    "PLACE_CONFLICT",
    "Another place already uses this name or network, or is already home.",
)
_PLACE_TOO_LARGE = (413, "REQUEST_BODY_TOO_LARGE", "Request body exceeds 16 KiB.")


def _place_error(refusal: tuple[int, str, str]) -> JSONResponse:
    status_code, code, message = refusal
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


def _places_pool(db: DatabaseManager | None):
    if db is None:
        raise _PlaceRefusal(*_PLACES_UNAVAILABLE)
    try:
        return db.pool(BUTLER_DB)
    except Exception as exc:
        raise _PlaceRefusal(*_PLACES_UNAVAILABLE) from exc


async def _read_private_body[T: BaseModel](
    request: Request,
    model: type[T],
    *,
    max_bytes: int,
    invalid: tuple[int, str, str],
    too_large: tuple[int, str, str],
) -> T:
    """Read at most ``max_bytes`` received octets and validate them without echoing input."""
    if request.url.query:
        raise _PlaceRefusal(*invalid)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk[: max_bytes + 1 - len(raw)])
        if len(raw) > max_bytes:
            raise _PlaceRefusal(*too_large)
    try:
        return model.model_validate(json.loads(bytes(raw).decode("utf-8")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        # ValidationError and JSONDecodeError are both ValueError subclasses.
        raise _PlaceRefusal(*invalid) from exc


async def _read_place_body[T: BaseModel](request: Request, model: type[T]) -> T:
    return await _read_private_body(
        request,
        model,
        max_bytes=_PLACES_MAX_BODY_BYTES,
        invalid=_INVALID_PLACE,
        too_large=_PLACE_TOO_LARGE,
    )


def _parse_place_id(place_id: str) -> UUID:
    try:
        parsed = UUID(place_id)
    except ValueError as exc:
        raise _PlaceRefusal(*_PLACE_NOT_FOUND) from exc
    if str(parsed) != place_id:
        raise _PlaceRefusal(*_PLACE_NOT_FOUND)
    return parsed


def _place_response(row: Any) -> OwnerPlaceResponse:
    return OwnerPlaceResponse(
        id=row["id"],
        label=row["label"],
        kind=row["kind"],
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        radius_m=row["radius_m"],
        aliases=list(row["aliases"] or []),
        ssids=list(row["ssids"] or []),
        ha_zone=row["ha_zone"],
        paused=row["paused"],
        version=row["version"],
        updated_at=row["updated_at"],
    )


def _same_place(row: Any, body: Any) -> bool:
    return (
        row["label"] == body.label
        and row["kind"] == body.kind
        and float(row["lat"]) == body.lat
        and float(row["lon"]) == body.lon
        and row["radius_m"] == body.radius_m
        and list(row["aliases"] or []) == body.aliases
        and list(row["ssids"] or []) == body.ssids
        and row["ha_zone"] == body.ha_zone
        and row["paused"] == body.paused
    )


def _place_args(body: Any) -> tuple[Any, ...]:
    return (
        body.label,
        body.kind,
        Decimal(repr(body.lat)),
        Decimal(repr(body.lon)),
        body.radius_m,
        body.aliases,
        body.ssids,
        body.ha_zone,
        body.paused,
    )


async def _conflicts(connection: Any, body: Any, place_id: UUID | None) -> bool:
    """True when another live place shares the label, an SSID, or the single home slot."""
    return await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM public.owner_places "
        "WHERE deleted_at IS NULL AND id IS DISTINCT FROM $1 "
        "AND (lower(label) = lower($2) OR ssids && $3::text[] "
        "OR ($4::text = 'home' AND kind = 'home')))",
        place_id,
        body.label,
        body.ssids,
        body.kind,
    )


async def _audit_place(
    connection: Any,
    operation: str,
    outcome: str,
    *,
    prior_version: int | None,
    new_version: int | None,
) -> None:
    """One allowlisted audit row: operation, outcome and versions, never place content."""
    await append_audit(
        connection,
        authenticated_principal(),
        f"home_owner_place_{operation}",
        metadata={
            "operation": operation,
            "outcome": outcome,
            "prior_version": prior_version,
            "new_version": new_version,
        },
        result="success"
        if outcome in {"created", "updated", "unchanged", "deleted"}
        else "refused",
    )
    logger.info("owner place %s outcome=%s", operation, outcome)


async def _place_transaction(pool: Any, decide: Any) -> Any:
    """Run ``decide`` under the registry lock in one transaction; map failures blind."""
    try:
        async with pool.acquire() as connection, audit_transaction(connection):
            await connection.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))",
                _PLACES_LOCK,
            )
            return await decide(connection)
    except _PlaceRefusal:
        raise
    except asyncpg.UniqueViolationError as exc:
        raise _PlaceRefusal(*_PLACE_CONFLICT) from exc
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        logger.error("owner place write failed: database unavailable")
        raise _PlaceRefusal(*_PLACES_UNAVAILABLE) from exc


@router.get(
    "/places",
    response_model=ApiResponse[list[OwnerPlaceResponse]],
    responses={503: {"model": ErrorResponse}},
)
async def list_owner_places(
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Every place the owner has not deleted, paused ones included, by label."""
    try:
        rows = await _places_pool(db).fetch(
            f"SELECT {_PLACE_COLUMNS} FROM public.owner_places "
            "WHERE deleted_at IS NULL ORDER BY lower(label), id"
        )
    except _PlaceRefusal as refusal:
        return _place_error((refusal.status_code, refusal.code, refusal.message))
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
        logger.error("owner place list failed: database unavailable")
        return _place_error(_PLACES_UNAVAILABLE)
    payload = ApiResponse[list[OwnerPlaceResponse]](data=[_place_response(row) for row in rows])
    return JSONResponse(
        content=payload.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
    )


@router.post(
    "/places",
    status_code=201,
    response_model=ApiResponse[OwnerPlaceResponse],
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_owner_place(
    request: Request,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Add a place; it takes part in resolution immediately unless created paused."""
    try:
        body = await _read_place_body(request, OwnerPlaceInput)
        pool = _places_pool(db)

        async def decide(connection: Any) -> Any:
            if await _conflicts(connection, body, None):
                await _audit_place(
                    connection, "create", "conflict", prior_version=None, new_version=None
                )
                return _PLACE_CONFLICT
            row = await connection.fetchrow(
                "INSERT INTO public.owner_places "
                "(label, kind, lat, lon, radius_m, aliases, ssids, ha_zone, paused) "
                "VALUES ($1, $2, $3, $4, $5, $6::text[], $7::text[], $8, $9) "
                f"RETURNING {_PLACE_COLUMNS}",
                *_place_args(body),
            )
            await _audit_place(connection, "create", "created", prior_version=None, new_version=1)
            return row

        result = await _place_transaction(pool, decide)
    except _PlaceRefusal as refusal:
        return _place_error((refusal.status_code, refusal.code, refusal.message))
    if isinstance(result, tuple):
        return _place_error(result)
    payload = ApiResponse[OwnerPlaceResponse](data=_place_response(result))
    return JSONResponse(
        status_code=201,
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.put(
    "/places/{place_id}",
    response_model=ApiResponse[OwnerPlaceResponse],
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def update_owner_place(
    request: Request,
    place_id: str,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Replace a place under compare-and-swap; pausing is an edit of ``paused``.

    An edit identical to the stored place is a no-op success even from a stale version;
    a changed edit from a stale version is ``409 VERSION_CONFLICT`` and writes nothing.
    """
    try:
        target = _parse_place_id(place_id)
        body = await _read_place_body(request, OwnerPlaceUpdate)
        pool = _places_pool(db)

        async def decide(connection: Any) -> Any:
            row = await connection.fetchrow(
                f"SELECT {_PLACE_COLUMNS} FROM public.owner_places "
                "WHERE id = $1 AND deleted_at IS NULL FOR UPDATE",
                target,
            )
            if row is None:
                await _audit_place(
                    connection, "update", "not_found", prior_version=None, new_version=None
                )
                return _PLACE_NOT_FOUND
            current = row["version"]
            if _same_place(row, body):
                await _audit_place(
                    connection, "update", "unchanged", prior_version=current, new_version=current
                )
                return row
            if body.expected_version != current:
                await _audit_place(
                    connection,
                    "update",
                    "version_conflict",
                    prior_version=current,
                    new_version=None,
                )
                return _PLACE_VERSION_CONFLICT
            if await _conflicts(connection, body, target):
                await _audit_place(
                    connection, "update", "conflict", prior_version=current, new_version=None
                )
                return _PLACE_CONFLICT
            updated = await connection.fetchrow(
                "UPDATE public.owner_places SET label = $2, kind = $3, lat = $4, lon = $5, "
                "radius_m = $6, aliases = $7::text[], ssids = $8::text[], ha_zone = $9, "
                "paused = $10, version = version + 1, updated_at = now() "
                f"WHERE id = $1 RETURNING {_PLACE_COLUMNS}",
                target,
                *_place_args(body),
            )
            await _audit_place(
                connection, "update", "updated", prior_version=current, new_version=current + 1
            )
            return updated

        result = await _place_transaction(pool, decide)
    except _PlaceRefusal as refusal:
        return _place_error((refusal.status_code, refusal.code, refusal.message))
    if isinstance(result, tuple):
        return _place_error(result)
    payload = ApiResponse[OwnerPlaceResponse](data=_place_response(result))
    return JSONResponse(
        content=payload.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
    )


@router.delete(
    "/places/{place_id}",
    status_code=204,
    response_model=None,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def delete_owner_place(
    place_id: str,
    request: Request,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Response:
    """Soft-delete a place at ``?expected_version=N``; its identity is never reused."""
    try:
        target = _parse_place_id(place_id)
        raw_version = request.query_params.get("expected_version", "")
        if (
            list(request.query_params) != ["expected_version"]
            or not raw_version.isascii()
            or not raw_version.isdigit()
            or int(raw_version) < 1
        ):
            raise _PlaceRefusal(*_INVALID_PLACE)
        expected = int(raw_version)
        pool = _places_pool(db)

        async def decide(connection: Any) -> Any:
            current = await connection.fetchval(
                "SELECT version FROM public.owner_places "
                "WHERE id = $1 AND deleted_at IS NULL FOR UPDATE",
                target,
            )
            if current is None:
                await _audit_place(
                    connection, "delete", "not_found", prior_version=None, new_version=None
                )
                return _PLACE_NOT_FOUND
            if current != expected:
                await _audit_place(
                    connection,
                    "delete",
                    "version_conflict",
                    prior_version=current,
                    new_version=None,
                )
                return _PLACE_VERSION_CONFLICT
            await connection.execute(
                "UPDATE public.owner_places SET deleted_at = now(), "
                "version = version + 1, updated_at = now() WHERE id = $1",
                target,
            )
            await _audit_place(
                connection, "delete", "deleted", prior_version=current, new_version=current + 1
            )
            return None

        result = await _place_transaction(pool, decide)
    except _PlaceRefusal as refusal:
        return _place_error((refusal.status_code, refusal.code, refusal.message))
    if isinstance(result, tuple):
        return _place_error(result)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# /api/home/settings/presence/owner-entities — owner presence entity settings
# (home:presence:owner_entities; REQ-home-presence-configuration-001..003; bu-jv1moa.7)
# ---------------------------------------------------------------------------
#
# Exactly one GET and one PUT, owner-only and content-blind like the place editor above.
# Writes serialize on a fixed advisory lock and compare-and-swap the existing state row's
# version.  The generic state API and the state_* MCP tools still read and overwrite this
# key without this validation or lock; this route's guarantees hold only among its own
# callers.  A PUT is a configuration receipt: the next scheduled presence producer run is
# the only thing that reads the new list.

_PRESENCE_KEY = "home:presence:owner_entities"
_PRESENCE_LOCK = "butlers:home:presence-owner-entities:v1"
_PRESENCE_MAX_BODY_BYTES = 32_768
_PRESENCE_AUDIT_ACTION = "home_presence_owner_entities_put"

_INVALID_PRESENCE = (422, "INVALID_REQUEST", "Presence settings request is invalid.")
_PRESENCE_UNAVAILABLE = (
    503,
    "PRESENCE_CONFIG_UNAVAILABLE",
    "Presence settings are unavailable.",
)
_PRESENCE_VERSION_CONFLICT = (
    409,
    "VERSION_CONFLICT",
    "Presence settings changed since they were loaded. Reload and try again.",
)
_PRESENCE_TOO_LARGE = (413, "REQUEST_BODY_TOO_LARGE", "Request body exceeds 32 KiB.")


def _presence_pool(db: DatabaseManager | None):
    if db is None:
        raise _PlaceRefusal(*_PRESENCE_UNAVAILABLE)
    try:
        return db.pool(BUTLER_DB)
    except Exception as exc:
        raise _PlaceRefusal(*_PRESENCE_UNAVAILABLE) from exc


def _stored_presence(row: Any) -> tuple[list[str], int] | None:
    """The stored list (ASCII-sorted) and row version; ``None`` when the value is malformed."""
    if row is None:
        return [], 0
    value = decode_jsonb(row["value"])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return sorted(value), row["version"]


def _presence_ok(entities: list[str], version: int) -> JSONResponse:
    payload = ApiResponse[PresenceOwnerEntitiesResponse](
        data=PresenceOwnerEntitiesResponse(
            configured=bool(entities), owner_entities=entities, version=version
        )
    )
    return JSONResponse(
        content=payload.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
    )


def _invalid_reference(count: int) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code="INVALID_REFERENCE",
            message="One or more entities have not been seen in Home Assistant.",
            details={"invalid_reference_count": count},
        )
    )
    return JSONResponse(
        status_code=422,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


async def _audit_presence(
    connection: Any,
    outcome: str,
    *,
    entities: list[str] | None,
    prior_version: int | None,
    new_version: int | None,
) -> None:
    """The one allowlisted audit event: counts, versions and outcome, never an entity id."""
    await append_audit(
        connection,
        authenticated_principal(),
        _PRESENCE_AUDIT_ACTION,
        metadata={
            "operation": "put",
            "configured": None if entities is None else bool(entities),
            "entity_count": None if entities is None else len(entities),
            "prior_version": prior_version,
            "new_version": new_version,
            "outcome": outcome,
        },
        result="success" if outcome in {"updated", "unchanged"} else "refused",
    )
    logger.info("owner presence entities put outcome=%s", outcome)


async def _audit_presence_best_effort(db: DatabaseManager | None, outcome: str) -> None:
    """Audit a refusal reached outside the write transaction; a failure here stays blind."""
    try:
        await _audit_presence(
            _presence_pool(db), outcome, entities=None, prior_version=None, new_version=None
        )
    except Exception:
        logger.warning("owner presence entities put outcome=%s audit unavailable", outcome)


@router.get(
    "/settings/presence/owner-entities",
    response_model=ApiResponse[PresenceOwnerEntitiesResponse],
    responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_presence_owner_entities(
    request: Request,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """The literal stored owner presence entity list, its row version and ``configured``."""
    try:
        if request.url.query:
            raise _PlaceRefusal(*_INVALID_PRESENCE)
        row = await _presence_pool(db).fetchrow(
            "SELECT value, version FROM state WHERE key = $1", _PRESENCE_KEY
        )
    except _PlaceRefusal as refusal:
        return _place_error((refusal.status_code, refusal.code, refusal.message))
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
        logger.error("owner presence entities read failed: database unavailable")
        return _place_error(_PRESENCE_UNAVAILABLE)
    stored = _stored_presence(row)
    if stored is None:
        return _place_error(_PRESENCE_UNAVAILABLE)
    return _presence_ok(*stored)


@router.put(
    "/settings/presence/owner-entities",
    response_model=ApiResponse[PresenceOwnerEntitiesResponse],
    responses={
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def put_presence_owner_entities(
    request: Request,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Replace the owner presence entity list under version compare-and-swap.

    An identical list is a no-op success at any ``expected_version``; a changed list from a
    stale version is ``409 VERSION_CONFLICT``.  Every id must already be a row in this
    butler's ``ha_entity_snapshot``; Home Assistant itself is never asked.
    """
    try:
        body = await _read_private_body(
            request,
            PresenceOwnerEntitiesUpdate,
            max_bytes=_PRESENCE_MAX_BODY_BYTES,
            invalid=_INVALID_PRESENCE,
            too_large=_PRESENCE_TOO_LARGE,
        )
    except _PlaceRefusal as refusal:
        # Malformed and oversized bodies are both terminal ``invalid_request`` outcomes.
        await _audit_presence_best_effort(db, "invalid_request")
        return _place_error((refusal.status_code, refusal.code, refusal.message))

    submitted = sorted(body.owner_entities)

    async def decide(connection: Any) -> Any:
        row = await connection.fetchrow(
            "SELECT value, version FROM state WHERE key = $1", _PRESENCE_KEY
        )
        stored = _stored_presence(row)
        if stored is None:
            await _audit_presence(
                connection,
                "unavailable",
                entities=submitted,
                prior_version=row["version"],
                new_version=None,
            )
            return _PRESENCE_UNAVAILABLE
        current, version = stored
        observed = await connection.fetchval(
            "SELECT count(*) FROM ha_entity_snapshot WHERE entity_id = ANY($1::text[])",
            submitted,
        )
        if observed != len(submitted):
            await _audit_presence(
                connection,
                "invalid_reference",
                entities=submitted,
                prior_version=version,
                new_version=None,
            )
            return len(submitted) - observed
        if submitted == current:
            await _audit_presence(
                connection,
                "unchanged",
                entities=submitted,
                prior_version=version,
                new_version=version,
            )
            return current, version
        if body.expected_version != version:
            await _audit_presence(
                connection,
                "version_conflict",
                entities=submitted,
                prior_version=version,
                new_version=None,
            )
            return _PRESENCE_VERSION_CONFLICT
        new_version = await state_set(connection, _PRESENCE_KEY, submitted)
        await _audit_presence(
            connection,
            "updated",
            entities=submitted,
            prior_version=version,
            new_version=new_version,
        )
        return submitted, new_version

    try:
        pool = _presence_pool(db)
        async with pool.acquire() as connection, audit_transaction(connection):
            await connection.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))",
                _PRESENCE_LOCK,
            )
            result = await decide(connection)
    except _PlaceRefusal as refusal:
        await _audit_presence_best_effort(db, "unavailable")
        return _place_error((refusal.status_code, refusal.code, refusal.message))
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
        logger.error("owner presence entities write failed: database unavailable")
        await _audit_presence_best_effort(db, "unavailable")
        return _place_error(_PRESENCE_UNAVAILABLE)
    if isinstance(result, int):
        return _invalid_reference(result)
    if len(result) == 3:
        return _place_error(result)
    return _presence_ok(*result)


# ---------------------------------------------------------------------------
# /api/home/presence/transitions — owner-only, read-only presence timeline
# (owner.place leave/enter projection over perception_timeline; bu-jv1moa.17)
# ---------------------------------------------------------------------------
#
# Reads the canonical perception timeline as the private owner reader and keeps only
# confirmed leave/enter edges. Heartbeats, establishing rows and retraction records are
# not edges. Late and retracted edges are returned flagged, never hidden, so history is
# complete; consumers skip them. Output is coordinate-free: the place is an opaque reference.
# The reader login is the owner-authorized activation; without it the route answers 503.

_PERCEPTION_READER_DSN_ENV = "BUTLERS_PERCEPTION_READER_DSN"
_TRANSITIONS_UNAVAILABLE = (
    503,
    "PRESENCE_TIMELINE_UNAVAILABLE",
    "The presence timeline is unavailable.",
)
_TRANSITIONS_WINDOW = timedelta(days=7)
_TRANSITIONS_CURSOR_SCOPE = "home-presence-transitions"
_TRANSITIONS_SCAN_PAGE = 100  # the timeline's largest page
_TRANSITIONS_MAX_SCANS = 20  # bounds one request to 2000 timeline rows


async def _open_presence_reader() -> Any | None:
    """A connection logged in as the private perception reader, or ``None`` when not activated."""
    dsn = (os.environ.get(_PERCEPTION_READER_DSN_ENV) or "").strip()
    if not dsn:
        return None
    connection = await asyncpg.connect(dsn, command_timeout=15)
    await register_jsonb_codec(connection)
    return connection


def _edge(item: dict[str, Any]) -> PresenceTransition | None:
    if item.get("operation") != "assert" or item.get("class") != "transition":
        return None
    before, after = item.get("from_value"), item.get("to_value")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    if before.get("presence") == after.get("presence"):
        return None
    direction = "leave" if after.get("presence") == "outside" else "enter"
    return PresenceTransition(
        id=UUID(str(item["id"])),
        direction=direction,
        place_ref=UUID(after["place_ref"]) if after.get("place_ref") else None,
        at=datetime.fromisoformat(item["at"]),
        confirmed_at=datetime.fromisoformat(item["confirmed_at"]),
        late=bool(item["late"]),
        retracted=bool(item["retracted"]),
        retraction_event_id=(
            UUID(str(item["retraction_event_id"])) if item.get("retraction_event_id") else None
        ),
    )


@router.get(
    "/presence/transitions",
    response_model=Page[PresenceTransition],
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def get_presence_transitions(
    request: Request,
    _owner: str = Depends(require_dashboard_owner_control),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(
        None, description="Opaque cursor from the previous page's next_cursor."
    ),
) -> Any:
    """Newest-first confirmed owner leave/enter edges for the last seven days."""
    before: UUID | None = None
    if cursor is not None:
        (decoded,) = decode_cursor(cursor, (UUID,), scope=_TRANSITIONS_CURSOR_SCOPE)
        before = decoded  # type: ignore[assignment]
    connection = None
    try:
        connection = await _open_presence_reader()
        if connection is None:
            return _place_error(_TRANSITIONS_UNAVAILABLE)
        now = datetime.now(UTC)
        store = PerceptionStore(connection)
        edges: list[PresenceTransition] = []
        scan_cursor = before
        more = False
        # The timeline holds heartbeats, resets and retractions too, so `limit` counts edges:
        # scan pages until enough edges are found, the week is exhausted or the scan cap is hit.
        for _ in range(_TRANSITIONS_MAX_SCANS):
            rows = await store.timeline(
                since=now - _TRANSITIONS_WINDOW,
                until=now,
                kind="owner.place",
                before=scan_cursor,
                limit=_TRANSITIONS_SCAN_PAGE,
            )
            for row in rows:
                scan_cursor = UUID(str(row["id"]))
                edge = _edge(row)
                if edge is not None:
                    edges.append(edge)
                    if len(edges) == limit:
                        more = True
                        break
            if len(edges) == limit:
                break
            if len(rows) < _TRANSITIONS_SCAN_PAGE:
                break
        else:
            more = True
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
        logger.error("presence timeline read failed: database unavailable")
        return _place_error(_TRANSITIONS_UNAVAILABLE)
    finally:
        if connection is not None:
            await connection.close()
    # An empty page with a cursor is real: the scan cap was hit before any edge was found.
    next_cursor = (
        encode_cursor((scan_cursor,), scope=_TRANSITIONS_CURSOR_SCOPE)
        if more and scan_cursor is not None
        else None
    )
    page = Page[PresenceTransition](items=edges, next_cursor=next_cursor, limit=limit)
    return JSONResponse(
        content=page.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )
