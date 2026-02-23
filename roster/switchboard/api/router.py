"""Switchboard butler endpoints.

Provides read-only endpoints for the routing log, butler registry, and
connector ingestion dashboard surfaces.
All data is queried directly from the switchboard butler's PostgreSQL
database via asyncpg.

Ingestion has moved to the Switchboard MCP server's ``ingest`` tool.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.config import load_config

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
    ConnectorEntry = _models.ConnectorEntry
    ConnectorSummary = _models.ConnectorSummary
    ConnectorStatsHourly = _models.ConnectorStatsHourly
    ConnectorStatsDaily = _models.ConnectorStatsDaily
    FanoutRow = _models.FanoutRow
    IngestionOverviewStats = _models.IngestionOverviewStats
else:
    raise RuntimeError("Failed to load switchboard API models")

logger = logging.getLogger(__name__)

# Period literal for query parameter validation
PeriodLiteral = Literal["24h", "7d", "30d"]
_PERIOD_HOURS: dict[str, int] = {"24h": 24, "7d": 168, "30d": 720}


def _normalize_jsonb_string_list(raw: Any) -> list[str]:
    """Normalize a JSONB value that may be a list, a JSON-serialized string, or a plain string.

    asyncpg decodes JSONB columns to native Python types.  When the stored value
    is a JSON array (``["a","b"]``) it returns a Python ``list``; when the stored
    value is a JSON string (``"a"`` or ``"a,b"``) it returns a Python ``str``.
    Calling ``list()`` on a string iterates its characters, which is the
    char-splitting regression this helper guards against.
    """
    if raw is None:
        return []

    # asyncpg already decoded the JSONB — handle the native Python types.
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str) and item.strip()]

    if isinstance(raw, str):
        candidate = raw.strip()
        if not candidate:
            return []
        # Try JSON parse first (handles stored-as-string serialized arrays).
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            decoded = candidate

        if isinstance(decoded, list):
            return [item for item in decoded if isinstance(item, str) and item.strip()]
        if isinstance(decoded, str):
            # Comma-separated plain string.
            return [tok.strip() for tok in decoded.split(",") if tok.strip()]

    return []


router = APIRouter(prefix="/api/switchboard", tags=["switchboard"])

BUTLER_DB = "switchboard"
_ROSTER_DIR = Path(__file__).resolve().parents[2]
_REGISTRY_MODULE_NAME = "switchboard_registry_tools"
_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "tools" / "registry" / "registry.py"


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


async def _register_missing_butler_from_roster(pool: Any, butler_name: str) -> bool:
    """Attempt to register an unknown butler using roster config metadata.

    Returns ``True`` when registration is attempted successfully, else ``False``
    (missing config or registration error).
    """
    config_dir = _ROSTER_DIR / butler_name
    toml_path = config_dir / "butler.toml"
    if not toml_path.exists():
        return False

    try:
        if _REGISTRY_MODULE_NAME in sys.modules:
            registry_module = sys.modules[_REGISTRY_MODULE_NAME]
        else:
            spec = importlib.util.spec_from_file_location(_REGISTRY_MODULE_NAME, _REGISTRY_PATH)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Failed to load registry tools from {_REGISTRY_PATH}")
            registry_module = importlib.util.module_from_spec(spec)
            sys.modules[_REGISTRY_MODULE_NAME] = registry_module
            spec.loader.exec_module(registry_module)

        register_butler = registry_module.register_butler
        config = load_config(config_dir)
        endpoint_url = f"http://localhost:{config.port}/sse"
        modules = list(config.modules.keys())
        capabilities = sorted(set(modules) | {"trigger"})
        await register_butler(
            pool,
            config.name,
            endpoint_url,
            config.description,
            modules,
            capabilities=capabilities,
        )
    except Exception:
        logger.warning(
            "Failed to auto-register missing butler %r from roster",
            butler_name,
            exc_info=True,
        )
        return False
    return True


def _row_to_connector_entry(r: dict) -> Any:
    """Convert a connector_registry asyncpg row dict to ConnectorEntry."""
    return ConnectorEntry(
        connector_type=r["connector_type"],
        endpoint_identity=r["endpoint_identity"],
        instance_id=str(r["instance_id"]) if r.get("instance_id") else None,
        version=r.get("version"),
        state=str(r.get("state") or "unknown"),
        error_message=r.get("error_message"),
        uptime_s=r.get("uptime_s"),
        last_heartbeat_at=str(r["last_heartbeat_at"]) if r.get("last_heartbeat_at") else None,
        first_seen_at=str(r["first_seen_at"]),
        registered_via=str(r.get("registered_via") or "self"),
        counter_messages_ingested=int(r.get("counter_messages_ingested") or 0),
        counter_messages_failed=int(r.get("counter_messages_failed") or 0),
        counter_source_api_calls=int(r.get("counter_source_api_calls") or 0),
        counter_checkpoint_saves=int(r.get("counter_checkpoint_saves") or 0),
        counter_dedupe_accepted=int(r.get("counter_dedupe_accepted") or 0),
        checkpoint_cursor=r.get("checkpoint_cursor"),
        checkpoint_updated_at=str(r["checkpoint_updated_at"])
        if r.get("checkpoint_updated_at")
        else None,
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
                modules=_normalize_jsonb_string_list(r.get("modules")),
                capabilities=_normalize_jsonb_string_list(r.get("capabilities")),
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
        registered = await _register_missing_butler_from_roster(pool, body.butler_name)
        if registered:
            row = await pool.fetchrow(
                "SELECT eligibility_state, last_seen_at FROM butler_registry WHERE name = $1",
                body.butler_name,
            )

    if row is None:
        raise HTTPException(status_code=404, detail=f"Butler '{body.butler_name}' not found")

    current_state: str = row["eligibility_state"]
    previous_last_seen_at = row["last_seen_at"]

    if current_state == "stale":
        # Transition stale → active and log the transition.
        # Guard with AND eligibility_state = 'stale' to avoid a TOCTOU race
        # where a concurrent operator quarantine overwrites the stale state
        # after our SELECT but before our UPDATE.
        result = await pool.execute(
            "UPDATE butler_registry"
            " SET last_seen_at = $1, eligibility_state = 'active',"
            "     eligibility_updated_at = $1"
            " WHERE name = $2 AND eligibility_state = 'stale'",
            now,
            body.butler_name,
        )
        rows_affected = int(result.split(" ")[-1]) if result else 0
        if rows_affected > 0:
            await pool.execute(
                "INSERT INTO butler_registry_eligibility_log"
                " (butler_name, previous_state, new_state, reason,"
                "  previous_last_seen_at, new_last_seen_at, observed_at)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7)",
                body.butler_name,
                "stale",
                "active",
                "health_restored",
                previous_last_seen_at,
                now,
                now,
            )
            new_state = "active"
        else:
            # Row was concurrently modified (e.g. quarantined); re-read
            # the current state and fall through to the last_seen_at update.
            re_read = await pool.fetchrow(
                "SELECT eligibility_state FROM butler_registry WHERE name = $1",
                body.butler_name,
            )
            current_state = re_read["eligibility_state"] if re_read else current_state
            await pool.execute(
                "UPDATE butler_registry SET last_seen_at = $1 WHERE name = $2",
                now,
                body.butler_name,
            )
            new_state = current_state
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


# ---------------------------------------------------------------------------
# GET /connectors — list all connectors
# ---------------------------------------------------------------------------


@router.get("/connectors", response_model=ApiResponse[list[ConnectorEntry]])
async def list_connectors(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ConnectorEntry]]:
    """List all connectors from the connector registry.

    Returns current state for each connector. Suitable for populating
    connector cards on the Overview and Connectors tabs, including health
    badge rows.

    Falls back gracefully to an empty list when the connector_registry
    table does not exist (degraded / partially migrated DB).
    """
    pool = _pool(db)

    try:
        rows = await pool.fetch(
            "SELECT connector_type, endpoint_identity, instance_id, version, state,"
            " error_message, uptime_s, last_heartbeat_at, first_seen_at, registered_via,"
            " counter_messages_ingested, counter_messages_failed, counter_source_api_calls,"
            " counter_checkpoint_saves, counter_dedupe_accepted,"
            " checkpoint_cursor, checkpoint_updated_at"
            " FROM connector_registry"
            " ORDER BY connector_type, endpoint_identity",
        )
    except Exception:
        logger.warning(
            "connector_registry table not available; returning empty list", exc_info=True
        )
        return ApiResponse[list[ConnectorEntry]](data=[])

    data = [_row_to_connector_entry(dict(row)) for row in rows]
    return ApiResponse[list[ConnectorEntry]](data=data)


# ---------------------------------------------------------------------------
# GET /connectors/summary — aggregate summary across all connectors
# ---------------------------------------------------------------------------


@router.get("/connectors/summary", response_model=ApiResponse[ConnectorSummary])
async def get_connectors_summary(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ConnectorSummary]:
    """Return aggregate connector health and volume summary.

    Drives the summary stats row at the top of the Connectors tab
    (total, online, stale, offline, ingested, failed, error rate).

    Falls back gracefully to a zero-value summary on DB errors.
    """
    pool = _pool(db)

    try:
        row = await pool.fetchrow(
            """
            SELECT
                count(*) AS total_connectors,
                count(*) FILTER (WHERE state = 'healthy') AS online_count,
                count(*) FILTER (WHERE state = 'degraded') AS stale_count,
                count(*) FILTER (WHERE state = 'error') AS offline_count,
                count(*) FILTER (WHERE state NOT IN ('healthy','degraded','error'))
                    AS unknown_count,
                coalesce(sum(counter_messages_ingested), 0) AS total_messages_ingested,
                coalesce(sum(counter_messages_failed), 0) AS total_messages_failed
            FROM connector_registry
            """,
        )
    except Exception:
        logger.warning(
            "connector_registry not available for summary; returning zeros", exc_info=True
        )
        return ApiResponse[ConnectorSummary](data=ConnectorSummary())

    if row is None:
        return ApiResponse[ConnectorSummary](data=ConnectorSummary())

    total_ingested = int(row["total_messages_ingested"] or 0)
    total_failed = int(row["total_messages_failed"] or 0)
    total_attempts = total_ingested + total_failed
    error_rate_pct = (total_failed / total_attempts * 100.0) if total_attempts > 0 else 0.0

    summary = ConnectorSummary(
        total_connectors=int(row["total_connectors"] or 0),
        online_count=int(row["online_count"] or 0),
        stale_count=int(row["stale_count"] or 0),
        offline_count=int(row["offline_count"] or 0),
        unknown_count=int(row["unknown_count"] or 0),
        total_messages_ingested=total_ingested,
        total_messages_failed=total_failed,
        error_rate_pct=round(error_rate_pct, 2),
    )
    return ApiResponse[ConnectorSummary](data=summary)


# ---------------------------------------------------------------------------
# GET /connectors/{connector_type}/{endpoint_identity} — connector detail
# ---------------------------------------------------------------------------


@router.get(
    "/connectors/{connector_type}/{endpoint_identity}",
    response_model=ApiResponse[ConnectorEntry],
)
async def get_connector_detail(
    connector_type: str,
    endpoint_identity: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ConnectorEntry]:
    """Return current state for a single connector.

    Raises 404 if the connector is not found in the registry.
    """
    pool = _pool(db)

    try:
        row = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity, instance_id, version, state,"
            " error_message, uptime_s, last_heartbeat_at, first_seen_at, registered_via,"
            " counter_messages_ingested, counter_messages_failed, counter_source_api_calls,"
            " counter_checkpoint_saves, counter_dedupe_accepted,"
            " checkpoint_cursor, checkpoint_updated_at"
            " FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "connector_registry not available for detail lookup %r/%r",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    return ApiResponse[ConnectorEntry](data=_row_to_connector_entry(dict(row)))


# ---------------------------------------------------------------------------
# GET /connectors/{connector_type}/{endpoint_identity}/stats — time-series stats
# ---------------------------------------------------------------------------


@router.get(
    "/connectors/{connector_type}/{endpoint_identity}/stats",
    response_model=ApiResponse[list[ConnectorStatsHourly] | list[ConnectorStatsDaily]],
)
async def get_connector_stats(
    connector_type: str,
    endpoint_identity: str,
    period: PeriodLiteral = Query("24h", description="Time window: 24h, 7d, or 30d"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ConnectorStatsHourly] | list[ConnectorStatsDaily]]:
    """Return time-series rollup stats for a single connector.

    - ``period=24h``: hourly rollup for the last 24 hours (connector_stats_hourly)
    - ``period=7d``: daily rollup for the last 7 days (connector_stats_daily)
    - ``period=30d``: daily rollup for the last 30 days (connector_stats_daily)

    Falls back gracefully to an empty list when rollup tables are missing.
    """
    pool = _pool(db)

    if period == "24h":
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)
        try:
            rows = await pool.fetch(
                "SELECT connector_type, endpoint_identity, hour,"
                " messages_ingested, messages_failed, source_api_calls, dedupe_accepted,"
                " heartbeat_count, healthy_count, degraded_count, error_count"
                " FROM connector_stats_hourly"
                " WHERE connector_type = $1 AND endpoint_identity = $2"
                " AND hour >= $3"
                " ORDER BY hour ASC",
                connector_type,
                endpoint_identity,
                cutoff,
            )
        except Exception:
            logger.warning(
                "connector_stats_hourly not available; returning empty list", exc_info=True
            )
            return ApiResponse(data=[])

        data: list = [
            ConnectorStatsHourly(
                connector_type=r["connector_type"],
                endpoint_identity=r["endpoint_identity"],
                hour=str(r["hour"]),
                messages_ingested=int(r["messages_ingested"] or 0),
                messages_failed=int(r["messages_failed"] or 0),
                source_api_calls=int(r["source_api_calls"] or 0),
                dedupe_accepted=int(r["dedupe_accepted"] or 0),
                heartbeat_count=int(r["heartbeat_count"] or 0),
                healthy_count=int(r["healthy_count"] or 0),
                degraded_count=int(r["degraded_count"] or 0),
                error_count=int(r["error_count"] or 0),
            )
            for r in rows
        ]
    else:
        days = 7 if period == "7d" else 30
        cutoff_date = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).date()
        try:
            rows = await pool.fetch(
                "SELECT connector_type, endpoint_identity, day,"
                " messages_ingested, messages_failed, source_api_calls, dedupe_accepted,"
                " heartbeat_count, healthy_count, degraded_count, error_count, uptime_pct"
                " FROM connector_stats_daily"
                " WHERE connector_type = $1 AND endpoint_identity = $2"
                " AND day >= $3"
                " ORDER BY day ASC",
                connector_type,
                endpoint_identity,
                cutoff_date,
            )
        except Exception:
            logger.warning(
                "connector_stats_daily not available; returning empty list", exc_info=True
            )
            return ApiResponse(data=[])

        data = [
            ConnectorStatsDaily(
                connector_type=r["connector_type"],
                endpoint_identity=r["endpoint_identity"],
                day=str(r["day"]),
                messages_ingested=int(r["messages_ingested"] or 0),
                messages_failed=int(r["messages_failed"] or 0),
                source_api_calls=int(r["source_api_calls"] or 0),
                dedupe_accepted=int(r["dedupe_accepted"] or 0),
                heartbeat_count=int(r["heartbeat_count"] or 0),
                healthy_count=int(r["healthy_count"] or 0),
                degraded_count=int(r["degraded_count"] or 0),
                error_count=int(r["error_count"] or 0),
                uptime_pct=float(r["uptime_pct"]) if r.get("uptime_pct") is not None else None,
            )
            for r in rows
        ]

    return ApiResponse(data=data)


# ---------------------------------------------------------------------------
# GET /connectors/{connector_type}/{endpoint_identity}/fanout — fanout breakdown
# ---------------------------------------------------------------------------


@router.get(
    "/connectors/{connector_type}/{endpoint_identity}/fanout",
    response_model=ApiResponse[list[FanoutRow]],
)
async def get_connector_fanout(
    connector_type: str,
    endpoint_identity: str,
    period: PeriodLiteral = Query("24h", description="Time window: 24h, 7d, or 30d"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[FanoutRow]]:
    """Return fanout distribution for a single connector.

    Aggregates message counts per target butler over the requested period.
    Used to populate the fanout distribution table in the Connectors tab detail
    view and the Overview tab fanout matrix.

    Falls back gracefully to an empty list when rollup tables are missing.
    """
    pool = _pool(db)

    days = _PERIOD_HOURS.get(period, 24) // 24 or 1
    cutoff_date = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).date()

    try:
        rows = await pool.fetch(
            "SELECT connector_type, endpoint_identity, target_butler,"
            " sum(message_count) AS message_count"
            " FROM connector_fanout_daily"
            " WHERE connector_type = $1 AND endpoint_identity = $2"
            " AND day >= $3"
            " GROUP BY connector_type, endpoint_identity, target_butler"
            " ORDER BY message_count DESC",
            connector_type,
            endpoint_identity,
            cutoff_date,
        )
    except Exception:
        logger.warning("connector_fanout_daily not available; returning empty list", exc_info=True)
        return ApiResponse[list[FanoutRow]](data=[])

    data = [
        FanoutRow(
            connector_type=r["connector_type"],
            endpoint_identity=r["endpoint_identity"],
            target_butler=r["target_butler"],
            message_count=int(r["message_count"] or 0),
        )
        for r in rows
    ]
    return ApiResponse[list[FanoutRow]](data=data)


# ---------------------------------------------------------------------------
# GET /ingestion/overview — overview aggregates
# ---------------------------------------------------------------------------


@router.get("/ingestion/overview", response_model=ApiResponse[IngestionOverviewStats])
async def get_ingestion_overview(
    period: PeriodLiteral = Query("24h", description="Time window: 24h, 7d, or 30d"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[IngestionOverviewStats]:
    """Return aggregate ingestion overview statistics.

    Drives the stat-row cards on the Overview tab:
    - total ingested
    - total skipped (tier 3)
    - total metadata-only (tier 2)
    - LLM calls saved (deterministic pre-LLM handling)
    - active connectors count

    Also provides tier breakdown counts for the donut chart.

    Data is sourced from connector_stats_hourly/daily rollup tables and
    connector_registry.  Falls back gracefully when tables are missing.
    """
    pool = _pool(db)

    hours = _PERIOD_HOURS.get(period, 24)

    # Active connectors: those with a heartbeat in the last N hours
    try:
        active_connectors = await pool.fetchval(
            "SELECT count(*)"
            " FROM connector_registry"
            " WHERE last_heartbeat_at >= now() - make_interval(hours => $1)"
            " AND state = 'healthy'",
            hours,
        )
        active_connectors = int(active_connectors or 0)
    except Exception:
        logger.warning("connector_registry not available for active count", exc_info=True)
        active_connectors = 0

    # Volume aggregates from rollup tables
    total_ingested = 0
    tier1_full_count = 0
    tier2_metadata_count = 0
    tier3_skip_count = 0

    if period == "24h":
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)
        try:
            agg = await pool.fetchrow(
                "SELECT coalesce(sum(messages_ingested), 0) AS total_ingested,"
                " coalesce(sum(messages_failed), 0) AS total_failed"
                " FROM connector_stats_hourly"
                " WHERE hour >= $1",
                cutoff,
            )
            if agg:
                total_ingested = int(agg["total_ingested"] or 0)
        except Exception:
            logger.warning("connector_stats_hourly not available for overview", exc_info=True)
    else:
        days = 7 if period == "7d" else 30
        cutoff_date = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).date()
        try:
            agg = await pool.fetchrow(
                "SELECT coalesce(sum(messages_ingested), 0) AS total_ingested,"
                " coalesce(sum(messages_failed), 0) AS total_failed"
                " FROM connector_stats_daily"
                " WHERE day >= $1",
                cutoff_date,
            )
            if agg:
                total_ingested = int(agg["total_ingested"] or 0)
        except Exception:
            logger.warning("connector_stats_daily not available for overview", exc_info=True)

    # Tier breakdown from message_inbox lifecycle/processing_metadata JSONB.
    # This is a best-effort query — if message_inbox is missing we skip gracefully.
    if period == "24h":
        inbox_cutoff: Any = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)
        inbox_where = "received_at >= $1"
        inbox_args: list = [inbox_cutoff]
    else:
        days = 7 if period == "7d" else 30
        inbox_cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
        inbox_where = "received_at >= $1"
        inbox_args = [inbox_cutoff]

    try:
        tier_row = await pool.fetchrow(
            f"""
            SELECT
                count(*) FILTER (
                    WHERE processing_metadata->>'policy_tier' = 'tier1'
                    OR processing_metadata->>'policy_tier' IS NULL
                ) AS tier1_full,
                count(*) FILTER (
                    WHERE processing_metadata->>'policy_tier' = 'tier2'
                ) AS tier2_metadata,
                count(*) FILTER (
                    WHERE processing_metadata->>'policy_tier' = 'tier3'
                ) AS tier3_skip
            FROM message_inbox
            WHERE {inbox_where}
            """,
            *inbox_args,
        )
        if tier_row:
            tier1_full_count = int(tier_row["tier1_full"] or 0)
            tier2_metadata_count = int(tier_row["tier2_metadata"] or 0)
            tier3_skip_count = int(tier_row["tier3_skip"] or 0)
    except Exception:
        logger.warning("message_inbox not available for tier breakdown; using zeros", exc_info=True)

    # LLM calls saved = tier2 + tier3 (messages handled without full LLM classification)
    llm_calls_saved = tier2_metadata_count + tier3_skip_count

    # Total skipped = tier3 messages
    total_skipped = tier3_skip_count
    # Total metadata-only = tier2 messages
    total_metadata_only = tier2_metadata_count

    overview = IngestionOverviewStats(
        period=period,
        total_ingested=total_ingested,
        total_skipped=total_skipped,
        total_metadata_only=total_metadata_only,
        llm_calls_saved=llm_calls_saved,
        active_connectors=active_connectors,
        tier1_full_count=tier1_full_count,
        tier2_metadata_count=tier2_metadata_count,
        tier3_skip_count=tier3_skip_count,
    )
    return ApiResponse[IngestionOverviewStats](data=overview)


# ---------------------------------------------------------------------------
# GET /ingestion/fanout — cross-connector fanout matrix
# ---------------------------------------------------------------------------


@router.get("/ingestion/fanout", response_model=ApiResponse[list[FanoutRow]])
async def get_ingestion_fanout(
    period: PeriodLiteral = Query("24h", description="Time window: 24h, 7d, or 30d"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[FanoutRow]]:
    """Return the cross-connector × butler fanout matrix for the Overview tab.

    Aggregates message counts per (connector_type, endpoint_identity, target_butler)
    over the requested period. Used to populate the fanout matrix table on the
    Overview tab.

    Falls back gracefully to an empty list when rollup tables are missing.
    """
    pool = _pool(db)

    days = _PERIOD_HOURS.get(period, 24) // 24 or 1
    cutoff_date = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).date()

    try:
        rows = await pool.fetch(
            "SELECT connector_type, endpoint_identity, target_butler,"
            " sum(message_count) AS message_count"
            " FROM connector_fanout_daily"
            " WHERE day >= $1"
            " GROUP BY connector_type, endpoint_identity, target_butler"
            " ORDER BY connector_type, endpoint_identity, message_count DESC",
            cutoff_date,
        )
    except Exception:
        logger.warning("connector_fanout_daily not available; returning empty list", exc_info=True)
        return ApiResponse[list[FanoutRow]](data=[])

    data = [
        FanoutRow(
            connector_type=r["connector_type"],
            endpoint_identity=r["endpoint_identity"],
            target_butler=r["target_butler"],
            message_count=int(r["message_count"] or 0),
        )
        for r in rows
    ]
    return ApiResponse[list[FanoutRow]](data=data)
