"""Home butler endpoints.

Provides endpoints for Home Assistant entity state, areas, command audit log,
snapshot freshness, device inventory, energy consumption, maintenance items,
and threshold configuration. All data is queried directly from the home
butler's PostgreSQL schema via asyncpg.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from httpx import AsyncClient as HttpxAsyncClient
from httpx import ConnectError as HttpxConnectError
from httpx import TimeoutException as HttpxTimeoutException

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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/home", tags=["home"])

BUTLER_DB = "home"

# Threshold state store keys
_THRESHOLD_BATTERY_KEY = "home:thresholds:battery"
_THRESHOLD_OFFLINE_KEY = "home:thresholds:offline_hours"
_THRESHOLD_COMFORT_DEFAULTS_KEY = "home:thresholds:comfort_defaults"
_THRESHOLD_COMFORT_DEVIATION_KEY = "home:thresholds:comfort_deviation"
_THRESHOLD_ENERGY_KEY = "home:thresholds:energy"


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
            friendly_name=(r["attributes"] or {}).get("friendly_name"),
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
    db: DatabaseManager = Depends(_get_db_manager),
) -> DeviceInventoryResponse:
    """List all known HA devices with current state, area, and health status.

    Supports optional filtering by domain, area name, and health status.
    Uses page-based pagination.
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


@router.get("/energy")
async def get_energy(
    period: str = Query("day", description="Aggregation period: 'day' or 'hour'"),
    start: str | None = Query(None, description="Start date (ISO 8601). Default: 7 days ago."),
    end: str | None = Query(None, description="End date (ISO 8601). Default: now."),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[dict[str, Any]]:
    """Return energy consumption time-series data.

    Proxies to HA REST API ``recorder/get_statistics_during_period``.
    Returns 503 if Home Assistant is unavailable.
    """
    pool = _pool(db)

    # Default date range
    now = datetime.now(UTC)
    end_dt = now
    start_dt = now - timedelta(days=7)

    if start is not None:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid 'start' datetime format. Expected ISO 8601.",
            )
    if end is not None:
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid 'end' datetime format. Expected ISO 8601.",
            )

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

    # Proxy to HA REST API
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    payload = {
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "period": period,
        "statistic_ids": statistic_ids,
        "types": ["sum", "mean"],
    }

    try:
        async with HttpxAsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{ha_url.rstrip('/')}/api/recorder/get_statistics_during_period",
                headers=headers,
                json=payload,
            )
    except (HttpxConnectError, HttpxTimeoutException) as exc:
        logger.warning("HA REST API unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="Home Assistant is unavailable")

    if resp.status_code != 200:
        logger.warning("HA energy API returned %d: %s", resp.status_code, resp.text)
        raise HTTPException(status_code=503, detail="Home Assistant is unavailable")

    # Parse HA statistics response format into EnergyDataPoint list
    # HA returns: {entity_id: [{start, end, sum, mean, ...}]}
    try:
        ha_data: dict[str, list[dict]] = resp.json()
    except Exception:
        raise HTTPException(status_code=503, detail="Home Assistant returned invalid data")

    # Aggregate by timestamp bucket
    buckets: dict[str, dict[str, float]] = {}
    for entity_id, stat_list in ha_data.items():
        for stat in stat_list:
            ts = stat.get("start") or stat.get("end")
            if ts is None:
                continue
            sum_val = stat.get("sum")
            if sum_val is not None:
                raw_value = sum_val
            else:
                mean_val = stat.get("mean")
                raw_value = mean_val if mean_val is not None else 0
            kwh = float(raw_value)
            if ts not in buckets:
                buckets[ts] = {}
            buckets[ts][entity_id] = buckets[ts].get(entity_id, 0) + kwh

    result = []
    for ts_str in sorted(buckets.keys()):
        device_map = buckets[ts_str]
        total_kwh = sum(device_map.values())
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
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
    start: str | None = Query(None, description="Start date (ISO 8601). Default: 7 days ago."),
    end: str | None = Query(None, description="End date (ISO 8601). Default: now."),
    db: DatabaseManager = Depends(_get_db_manager),
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

    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    payload = {
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "period": "day",
        "statistic_ids": statistic_ids,
        "types": ["sum"],
    }

    try:
        async with HttpxAsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{ha_url.rstrip('/')}/api/recorder/get_statistics_during_period",
                headers=headers,
                json=payload,
            )
    except (HttpxConnectError, HttpxTimeoutException) as exc:
        logger.warning("HA REST API unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="Home Assistant is unavailable")

    if resp.status_code != 200:
        raise HTTPException(status_code=503, detail="Home Assistant is unavailable")

    try:
        ha_data: dict[str, list[dict]] = resp.json()
    except Exception:
        raise HTTPException(status_code=503, detail="Home Assistant returned invalid data")

    def _get_stat_value(s: dict) -> float:
        value = s.get("sum")
        if value is None:
            value = s.get("mean")
        if value is None:
            return 0.0
        return float(value)

    # Sum per device
    device_totals: dict[str, float] = {}
    for entity_id, stat_list in ha_data.items():
        total = sum(_get_stat_value(s) for s in stat_list)
        device_totals[entity_id] = total

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
    db: DatabaseManager = Depends(_get_db_manager),
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
            )
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
    body: MaintenanceItemCreateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
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
            )
        if "does not exist" in err_str or "relation" in err_str:
            raise HTTPException(
                status_code=503,
                detail="Maintenance items table not found. Run migrations first.",
            )
        raise

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create maintenance item")

    return _row_to_maintenance_item(row).model_dump(mode="json")


# ---------------------------------------------------------------------------
# POST /api/home/maintenance/{item_id}/complete — complete a maintenance item
# ---------------------------------------------------------------------------


@router.post("/maintenance/{item_id}/complete")
async def complete_maintenance_item(
    item_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
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
            )
        raise

    if row is None:
        raise HTTPException(status_code=404, detail=f"Maintenance item not found: {item_id}")

    return _row_to_maintenance_item(row).model_dump(mode="json")


# ---------------------------------------------------------------------------
# DELETE /api/home/maintenance/{item_id} — delete maintenance item
# ---------------------------------------------------------------------------


@router.delete("/maintenance/{item_id}", status_code=204)
async def delete_maintenance_item(
    item_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
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
            )
        raise

    if result is None:
        raise HTTPException(status_code=404, detail=f"Maintenance item not found: {item_id}")


# ---------------------------------------------------------------------------
# Threshold helpers
# ---------------------------------------------------------------------------


_DEFAULT_THRESHOLDS = {
    "battery": {"critical": 10, "warning": 20, "info": 30},
    "offline_hours": {"critical": 24, "warning": 1},
    "comfort_defaults": {
        "temp_min_f": 68,
        "temp_max_f": 76,
        "humidity_min": 30,
        "humidity_max": 60,
        "co2_max_ppm": 1000,
    },
    "comfort_deviation": {
        "minor_temp_f": 2,
        "moderate_temp_f": 5,
        "minor_humidity": 10,
        "moderate_humidity": 20,
        "critical_temp_low_f": 60,
        "critical_temp_high_f": 85,
        "critical_co2_ppm": 1500,
        "critical_humidity_low": 15,
        "critical_humidity_high": 80,
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
    db: DatabaseManager = Depends(_get_db_manager),
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
    body: ThresholdUpdateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
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
                json.dumps(value),
            )
        except Exception as exc:
            logger.warning("Failed to persist threshold key %s: %s", store_key, exc)
            raise HTTPException(
                status_code=503,
                detail=f"Failed to persist threshold: {store_key}",
            )

    # Merge into current and return
    current.update(update_map)
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
