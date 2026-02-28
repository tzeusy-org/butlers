"""Switchboard butler endpoints.

Provides read-only endpoints for the routing log, butler registry,
connector ingestion dashboard surfaces, and triage rules.
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
import uuid
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query

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
    SetEligibilityRequest = _models.SetEligibilityRequest
    SetEligibilityResponse = _models.SetEligibilityResponse
    ConnectorEntry = _models.ConnectorEntry
    ConnectorSummary = _models.ConnectorSummary
    ConnectorStatsHourly = _models.ConnectorStatsHourly
    ConnectorStatsDaily = _models.ConnectorStatsDaily
    FanoutRow = _models.FanoutRow
    IngestionOverviewStats = _models.IngestionOverviewStats
    BackfillJobEntry = _models.BackfillJobEntry
    BackfillJobSummary = _models.BackfillJobSummary
    CreateBackfillJobRequest = _models.CreateBackfillJobRequest
    BackfillLifecycleResponse = _models.BackfillLifecycleResponse
    TriageRule = _models.TriageRule
    TriageRuleCreate = _models.TriageRuleCreate
    TriageRuleUpdate = _models.TriageRuleUpdate
    TriageRuleTestRequest = _models.TriageRuleTestRequest
    TriageRuleTestResult = _models.TriageRuleTestResult
    TriageRuleTestResponse = _models.TriageRuleTestResponse
    ThreadAffinitySettings = _models.ThreadAffinitySettings
    ThreadAffinitySettingsUpdate = _models.ThreadAffinitySettingsUpdate
    ThreadOverrideUpsert = _models.ThreadOverrideUpsert
    ThreadOverrideEntry = _models.ThreadOverrideEntry
    EligibilitySegment = _models.EligibilitySegment
    EligibilityHistoryResponse = _models.EligibilityHistoryResponse
    RoutingInstruction = _models.RoutingInstruction
    RoutingInstructionCreate = _models.RoutingInstructionCreate
    RoutingInstructionUpdate = _models.RoutingInstructionUpdate
    validate_condition = _models.validate_condition
    validate_action = _models.validate_action
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
    - ``stale`` → ``active``: transition logged with reason ``health_restored``
    - ``quarantined`` → ``active``: auto-recovery logged with reason ``heartbeat_recovery``,
      clears ``quarantined_at`` and ``quarantine_reason``
    - ``active``: ``last_seen_at`` updated, state unchanged
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
    elif current_state == "quarantined":
        # Transition quarantined → active: CAS guard on eligibility_state to avoid
        # TOCTOU race with a concurrent operator re-quarantine.
        result = await pool.execute(
            "UPDATE butler_registry"
            " SET last_seen_at = $1, eligibility_state = 'active',"
            "     eligibility_updated_at = $1,"
            "     quarantined_at = NULL, quarantine_reason = NULL"
            " WHERE name = $2 AND eligibility_state = 'quarantined'",
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
                "quarantined",
                "active",
                "heartbeat_recovery",
                previous_last_seen_at,
                now,
                now,
            )
            new_state = "active"
        else:
            # Row was concurrently modified; re-read and fall through to last_seen_at
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
        # Active: only update last_seen_at, do not change state
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
# POST /registry/{name}/eligibility — operator eligibility state transition
# ---------------------------------------------------------------------------


@router.post(
    "/registry/{name}/eligibility",
    response_model=ApiResponse[SetEligibilityResponse],
)
async def set_butler_eligibility(
    name: str,
    body: SetEligibilityRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SetEligibilityResponse]:
    """Transition a butler's eligibility state (operator action).

    Allows an operator to move a butler between eligibility states, e.g.
    un-quarantining a butler that has recovered.  All transitions are
    audited to the eligibility log.
    """
    pool = _pool(db)
    now = datetime.datetime.now(datetime.UTC)

    row = await pool.fetchrow(
        "SELECT eligibility_state, last_seen_at FROM butler_registry WHERE name = $1",
        name,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Butler '{name}' not found in registry")

    previous_state: str = row["eligibility_state"]
    if previous_state == body.eligibility_state:
        return ApiResponse[SetEligibilityResponse](
            data=SetEligibilityResponse(
                name=name,
                previous_state=previous_state,
                new_state=previous_state,
            )
        )

    # Build update fields
    update_fields = {
        "eligibility_state": body.eligibility_state,
        "eligibility_updated_at": now,
    }
    if body.eligibility_state != "quarantined":
        update_fields["quarantined_at"] = None
        update_fields["quarantine_reason"] = None

    await pool.execute(
        "UPDATE butler_registry"
        " SET eligibility_state = $1,"
        "     eligibility_updated_at = $2,"
        "     quarantined_at = $3,"
        "     quarantine_reason = $4"
        " WHERE name = $5",
        body.eligibility_state,
        now,
        update_fields.get("quarantined_at", now),
        update_fields.get("quarantine_reason"),
        name,
    )

    # Audit the transition
    try:
        await pool.execute(
            """
            INSERT INTO butler_registry_eligibility_log (
                butler_name, previous_state, new_state, reason,
                previous_last_seen_at, new_last_seen_at, observed_at
            )
            VALUES ($1, $2, $3, $4, $5, $5, $6)
            """,
            name,
            previous_state,
            body.eligibility_state,
            "operator_action",
            row["last_seen_at"],
            now,
        )
    except Exception:
        logger.warning("Failed to write eligibility audit log for %s", name, exc_info=True)

    logger.info(
        "Operator eligibility transition for butler %r: %s → %s",
        name,
        previous_state,
        body.eligibility_state,
    )

    return ApiResponse[SetEligibilityResponse](
        data=SetEligibilityResponse(
            name=name,
            previous_state=previous_state,
            new_state=body.eligibility_state,
        )
    )


# ---------------------------------------------------------------------------
# GET /registry/{name}/eligibility-history — 24h eligibility timeline
# ---------------------------------------------------------------------------


@router.get(
    "/registry/{name}/eligibility-history",
    response_model=ApiResponse[EligibilityHistoryResponse],
)
async def get_eligibility_history(
    name: str,
    hours: int = Query(default=24, ge=1, le=168),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[EligibilityHistoryResponse]:
    """Return an eligibility timeline for a butler over the requested window.

    Builds Datadog-style segments from the eligibility audit log.  Each segment
    represents a contiguous period in one eligibility state.
    """
    pool = _pool(db)
    now = datetime.datetime.now(datetime.UTC)
    window_start = now - datetime.timedelta(hours=hours)

    # Verify butler exists
    current = await pool.fetchrow(
        "SELECT eligibility_state FROM butler_registry WHERE name = $1",
        name,
    )
    if current is None:
        raise HTTPException(status_code=404, detail=f"Butler '{name}' not found in registry")

    current_state: str = current["eligibility_state"]

    rows = await pool.fetch(
        "SELECT previous_state, new_state, observed_at"
        " FROM butler_registry_eligibility_log"
        " WHERE butler_name = $1 AND observed_at >= $2"
        " ORDER BY observed_at ASC",
        name,
        window_start,
    )

    def _ts(dt: datetime.datetime) -> str:
        return dt.isoformat()

    segments: list[EligibilitySegment] = []

    if not rows:
        # No transitions — single segment covering the full window
        segments.append(
            EligibilitySegment(
                state=current_state,
                start_at=_ts(window_start),
                end_at=_ts(now),
            )
        )
    else:
        # First segment: window_start → first transition
        first_row = rows[0]
        segments.append(
            EligibilitySegment(
                state=first_row["previous_state"],
                start_at=_ts(window_start),
                end_at=_ts(first_row["observed_at"]),
            )
        )
        # Middle segments: each transition's new_state until the next transition
        for i, row in enumerate(rows):
            seg_start = row["observed_at"]
            seg_end = rows[i + 1]["observed_at"] if i + 1 < len(rows) else now
            segments.append(
                EligibilitySegment(
                    state=row["new_state"],
                    start_at=_ts(seg_start),
                    end_at=_ts(seg_end),
                )
            )

    return ApiResponse[EligibilityHistoryResponse](
        data=EligibilityHistoryResponse(
            butler_name=name,
            segments=segments,
            window_start=_ts(window_start),
            window_end=_ts(now),
        )
    )


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

    Note: This endpoint queries the ``connector_fanout_daily`` table, which has
    daily granularity only. ``period="24h"`` therefore covers the current and
    previous calendar day (yesterday + today UTC), not a strict rolling 24-hour
    window. This is consistent with the table schema; the ``/stats`` endpoint
    uses hourly granularity for the 24h window.

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

    ``total_ingested`` is derived from ``message_inbox`` as the sum of all
    tier1 + tier2 + tier3 messages in the period.  This ensures that messages
    processed by internal modules (which do not write to the connector rollup
    tables) are counted correctly.  The connector rollup tables
    (``connector_stats_hourly`` / ``connector_stats_daily``) are still
    populated by external connectors and are used by the per-connector stats
    endpoints.

    Falls back gracefully when tables are missing.
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

    tier1_full_count = 0
    tier2_metadata_count = 0
    tier3_skip_count = 0

    # Compute inbox_cutoff for both tier breakdown and total_ingested.
    inbox_cutoff: Any = datetime.datetime.now(datetime.UTC) - (
        datetime.timedelta(hours=24)
        if period == "24h"
        else datetime.timedelta(days=7 if period == "7d" else 30)
    )

    # Tier breakdown from message_inbox processing_metadata JSONB.
    # total_ingested is derived here as tier1+tier2+tier3 so that messages
    # ingested via internal modules (which bypass the connector rollup tables)
    # are always counted.  No double-counting occurs because each message_inbox
    # row has exactly one policy_tier assignment.
    try:
        tier_row = await pool.fetchrow(
            """
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
            WHERE received_at >= $1
            """,
            inbox_cutoff,
        )
        if tier_row:
            tier1_full_count = int(tier_row["tier1_full"] or 0)
            tier2_metadata_count = int(tier_row["tier2_metadata"] or 0)
            tier3_skip_count = int(tier_row["tier3_skip"] or 0)
    except Exception:
        logger.warning("message_inbox not available for tier breakdown; using zeros", exc_info=True)

    # total_ingested = all rows in message_inbox for the period (tier1+tier2+tier3).
    # This is the canonical source of truth for ingestion volume.
    total_ingested = tier1_full_count + tier2_metadata_count + tier3_skip_count

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

    Note: This endpoint queries the ``connector_fanout_daily`` table, which has
    daily granularity only. ``period="24h"`` therefore covers the current and
    previous calendar day (yesterday + today UTC), not a strict rolling 24-hour
    window.

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


# ---------------------------------------------------------------------------
# Backfill helpers
# ---------------------------------------------------------------------------

_BACKFILL_ALLOWED_STATUSES = frozenset(
    {"pending", "active", "paused", "completed", "cancelled", "cost_capped", "error"}
)

# Status transitions allowed per lifecycle action
_BACKFILL_PAUSE_FROM = frozenset({"pending", "active"})
_BACKFILL_CANCEL_FROM = frozenset({"pending", "active", "paused", "cost_capped", "error"})
_BACKFILL_RESUME_FROM = frozenset({"paused"})


def _row_to_backfill_summary(r: Any) -> Any:
    """Convert an asyncpg row dict to BackfillJobSummary."""
    return BackfillJobSummary(
        id=str(r["id"]),
        connector_type=str(r["connector_type"]),
        endpoint_identity=str(r["endpoint_identity"]),
        target_categories=_normalize_jsonb_string_list(r.get("target_categories")),
        date_from=str(r["date_from"]),
        date_to=str(r["date_to"]),
        rate_limit_per_hour=int(r.get("rate_limit_per_hour") or 100),
        daily_cost_cap_cents=int(r.get("daily_cost_cap_cents") or 500),
        status=str(r.get("status") or "pending"),
        rows_processed=int(r.get("rows_processed") or 0),
        rows_skipped=int(r.get("rows_skipped") or 0),
        cost_spent_cents=int(r.get("cost_spent_cents") or 0),
        error=r.get("error"),
        created_at=str(r["created_at"]),
        started_at=str(r["started_at"]) if r.get("started_at") else None,
        completed_at=str(r["completed_at"]) if r.get("completed_at") else None,
        updated_at=str(r["updated_at"]),
    )


def _row_to_backfill_entry(r: Any) -> Any:
    """Convert an asyncpg row dict to BackfillJobEntry (full detail including cursor)."""
    cursor_raw = r.get("cursor")
    if isinstance(cursor_raw, str):
        try:
            cursor_raw = json.loads(cursor_raw)
        except (json.JSONDecodeError, ValueError):
            cursor_raw = None
    return BackfillJobEntry(
        id=str(r["id"]),
        connector_type=str(r["connector_type"]),
        endpoint_identity=str(r["endpoint_identity"]),
        target_categories=_normalize_jsonb_string_list(r.get("target_categories")),
        date_from=str(r["date_from"]),
        date_to=str(r["date_to"]),
        rate_limit_per_hour=int(r.get("rate_limit_per_hour") or 100),
        daily_cost_cap_cents=int(r.get("daily_cost_cap_cents") or 500),
        status=str(r.get("status") or "pending"),
        cursor=cursor_raw if isinstance(cursor_raw, dict) else None,
        rows_processed=int(r.get("rows_processed") or 0),
        rows_skipped=int(r.get("rows_skipped") or 0),
        cost_spent_cents=int(r.get("cost_spent_cents") or 0),
        error=r.get("error"),
        created_at=str(r["created_at"]),
        started_at=str(r["started_at"]) if r.get("started_at") else None,
        completed_at=str(r["completed_at"]) if r.get("completed_at") else None,
        updated_at=str(r["updated_at"]),
    )


# ---------------------------------------------------------------------------
# GET /backfill — list backfill jobs
# ---------------------------------------------------------------------------


@router.get("/backfill", response_model=PaginatedResponse[BackfillJobSummary])
async def list_backfill_jobs(
    connector_type: str | None = Query(None, description="Filter by connector type"),
    endpoint_identity: str | None = Query(None, description="Filter by endpoint identity"),
    status: str | None = Query(None, description="Filter by job status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[BackfillJobSummary]:
    """Return a paginated list of backfill jobs.

    Supports filtering by connector_type, endpoint_identity, and status.
    Jobs are ordered by created_at descending (newest first).
    """
    pool = _pool(db)

    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if connector_type is not None:
        conditions.append(f"connector_type = ${idx}")
        params.append(connector_type)
        idx += 1

    if endpoint_identity is not None:
        conditions.append(f"endpoint_identity = ${idx}")
        params.append(endpoint_identity)
        idx += 1

    if status is not None:
        if status not in _BACKFILL_ALLOWED_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Allowed: {sorted(_BACKFILL_ALLOWED_STATUSES)}",
            )
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_sql = f"SELECT count(*) FROM switchboard.backfill_jobs {where_clause}"
    list_sql = (
        f"SELECT id, connector_type, endpoint_identity, target_categories,"
        f" date_from, date_to, rate_limit_per_hour, daily_cost_cap_cents,"
        f" status, rows_processed, rows_skipped, cost_spent_cents, error,"
        f" created_at, started_at, completed_at, updated_at"
        f" FROM switchboard.backfill_jobs {where_clause}"
        f" ORDER BY created_at DESC"
        f" LIMIT ${idx} OFFSET ${idx + 1}"
    )
    list_params = params + [limit, offset]

    try:
        total = int(await pool.fetchval(count_sql, *params) or 0)
        rows = await pool.fetch(list_sql, *list_params)
    except Exception:
        logger.warning("backfill_jobs table not available; returning empty list", exc_info=True)
        return PaginatedResponse[BackfillJobSummary](
            data=[],
            meta=PaginationMeta(total=0, offset=offset, limit=limit),
        )

    data = [_row_to_backfill_summary(r) for r in rows]
    return PaginatedResponse[BackfillJobSummary](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# POST /backfill — create a new backfill job
# ---------------------------------------------------------------------------


@router.post("/backfill", response_model=ApiResponse[BackfillJobEntry], status_code=201)
async def create_backfill_job(
    body: CreateBackfillJobRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BackfillJobEntry]:
    """Create a new backfill job and queue it as pending.

    The job is inserted into switchboard.backfill_jobs with status=pending.
    The connector will pick it up on its next poll cycle.

    Returns the created job with its assigned id.
    """
    pool = _pool(db)

    job_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.UTC)
    target_categories_json = json.dumps(body.target_categories)

    try:
        row = await pool.fetchrow(
            "INSERT INTO switchboard.backfill_jobs"
            " (id, connector_type, endpoint_identity, target_categories,"
            "  date_from, date_to, rate_limit_per_hour, daily_cost_cap_cents,"
            "  status, rows_processed, rows_skipped, cost_spent_cents,"
            "  created_at, updated_at)"
            " VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8,'pending',0,0,0,$9,$9)"
            " RETURNING id, connector_type, endpoint_identity, target_categories,"
            "   date_from, date_to, rate_limit_per_hour, daily_cost_cap_cents,"
            "   status, cursor, rows_processed, rows_skipped, cost_spent_cents,"
            "   error, created_at, started_at, completed_at, updated_at",
            job_id,
            body.connector_type,
            body.endpoint_identity,
            target_categories_json,
            body.date_from,
            body.date_to,
            body.rate_limit_per_hour,
            body.daily_cost_cap_cents,
            now,
        )
    except Exception:
        logger.exception("Failed to create backfill job")
        raise HTTPException(status_code=503, detail="Failed to create backfill job")

    if row is None:
        raise HTTPException(status_code=503, detail="No row returned after insert")

    return ApiResponse[BackfillJobEntry](data=_row_to_backfill_entry(row))


# ---------------------------------------------------------------------------
# GET /backfill/{job_id} — get backfill job detail
# ---------------------------------------------------------------------------


@router.get("/backfill/{job_id}", response_model=ApiResponse[BackfillJobEntry])
async def get_backfill_job(
    job_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BackfillJobEntry]:
    """Return the full details of a single backfill job by id.

    Returns 404 if the job does not exist.
    Returns 503 on database error.
    """
    pool = _pool(db)

    try:
        row = await pool.fetchrow(
            "SELECT id, connector_type, endpoint_identity, target_categories,"
            "   date_from, date_to, rate_limit_per_hour, daily_cost_cap_cents,"
            "   status, cursor, rows_processed, rows_skipped, cost_spent_cents,"
            "   error, created_at, started_at, completed_at, updated_at"
            " FROM switchboard.backfill_jobs"
            " WHERE id = $1",
            job_id,
        )
    except Exception:
        logger.exception("Failed to fetch backfill job %s", job_id)
        raise HTTPException(status_code=503, detail="Failed to fetch backfill job")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Backfill job '{job_id}' not found")

    return ApiResponse[BackfillJobEntry](data=_row_to_backfill_entry(row))


# ---------------------------------------------------------------------------
# PATCH /backfill/{job_id}/pause
# ---------------------------------------------------------------------------


@router.patch("/backfill/{job_id}/pause", response_model=ApiResponse[BackfillLifecycleResponse])
async def pause_backfill_job(
    job_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BackfillLifecycleResponse]:
    """Pause an active or pending backfill job.

    Only jobs in 'pending' or 'active' state may be paused.
    Returns 404 if the job does not exist.
    Returns 409 if the job is in a terminal or incompatible state.
    Returns 503 on database error.
    """
    pool = _pool(db)

    try:
        row = await pool.fetchrow(
            "SELECT status FROM switchboard.backfill_jobs WHERE id = $1",
            job_id,
        )
    except Exception:
        logger.exception("Failed to fetch backfill job %s for pause", job_id)
        raise HTTPException(status_code=503, detail="Failed to fetch backfill job")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Backfill job '{job_id}' not found")

    current_status = str(row["status"])
    if current_status not in _BACKFILL_PAUSE_FROM:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot pause job in status '{current_status}'. "
            f"Must be one of: {sorted(_BACKFILL_PAUSE_FROM)}",
        )

    try:
        await pool.fetchrow(
            "UPDATE switchboard.backfill_jobs SET status='paused', updated_at=now() WHERE id = $1",
            job_id,
        )
    except Exception:
        logger.exception("Failed to pause backfill job %s", job_id)
        raise HTTPException(status_code=503, detail="Failed to pause backfill job")

    return ApiResponse[BackfillLifecycleResponse](
        data=BackfillLifecycleResponse(job_id=job_id, status="paused")
    )


# ---------------------------------------------------------------------------
# PATCH /backfill/{job_id}/cancel
# ---------------------------------------------------------------------------


@router.patch("/backfill/{job_id}/cancel", response_model=ApiResponse[BackfillLifecycleResponse])
async def cancel_backfill_job(
    job_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BackfillLifecycleResponse]:
    """Cancel a backfill job.

    Jobs in 'pending', 'active', 'paused', 'cost_capped', or 'error' state may
    be cancelled.  Terminal states 'completed' and 'cancelled' cannot be
    cancelled again.
    Returns 404 if the job does not exist.
    Returns 409 if the job is in a terminal state that disallows cancellation.
    Returns 503 on database error.
    """
    pool = _pool(db)

    try:
        row = await pool.fetchrow(
            "SELECT status FROM switchboard.backfill_jobs WHERE id = $1",
            job_id,
        )
    except Exception:
        logger.exception("Failed to fetch backfill job %s for cancel", job_id)
        raise HTTPException(status_code=503, detail="Failed to fetch backfill job")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Backfill job '{job_id}' not found")

    current_status = str(row["status"])
    if current_status not in _BACKFILL_CANCEL_FROM:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel job in status '{current_status}'. "
            f"Must be one of: {sorted(_BACKFILL_CANCEL_FROM)}",
        )

    try:
        await pool.fetchrow(
            "UPDATE switchboard.backfill_jobs"
            " SET status='cancelled', updated_at=now()"
            " WHERE id = $1",
            job_id,
        )
    except Exception:
        logger.exception("Failed to cancel backfill job %s", job_id)
        raise HTTPException(status_code=503, detail="Failed to cancel backfill job")

    return ApiResponse[BackfillLifecycleResponse](
        data=BackfillLifecycleResponse(job_id=job_id, status="cancelled")
    )


# ---------------------------------------------------------------------------
# PATCH /backfill/{job_id}/resume
# ---------------------------------------------------------------------------


@router.patch("/backfill/{job_id}/resume", response_model=ApiResponse[BackfillLifecycleResponse])
async def resume_backfill_job(
    job_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BackfillLifecycleResponse]:
    """Resume a paused backfill job.

    Only jobs in 'paused' state may be resumed.
    Returns 404 if the job does not exist.
    Returns 409 if the job is not in 'paused' state.
    Returns 503 on database error.
    """
    pool = _pool(db)

    try:
        row = await pool.fetchrow(
            "SELECT status FROM switchboard.backfill_jobs WHERE id = $1",
            job_id,
        )
    except Exception:
        logger.exception("Failed to fetch backfill job %s for resume", job_id)
        raise HTTPException(status_code=503, detail="Failed to fetch backfill job")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Backfill job '{job_id}' not found")

    current_status = str(row["status"])
    if current_status not in _BACKFILL_RESUME_FROM:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resume job in status '{current_status}'. "
            f"Must be one of: {sorted(_BACKFILL_RESUME_FROM)}",
        )

    try:
        await pool.fetchrow(
            "UPDATE switchboard.backfill_jobs SET status='pending', updated_at=now() WHERE id = $1",
            job_id,
        )
    except Exception:
        logger.exception("Failed to resume backfill job %s", job_id)
        raise HTTPException(status_code=503, detail="Failed to resume backfill job")

    return ApiResponse[BackfillLifecycleResponse](
        data=BackfillLifecycleResponse(job_id=job_id, status="pending")
    )


# ---------------------------------------------------------------------------
# GET /backfill/{job_id}/progress — backfill progress metrics
# ---------------------------------------------------------------------------


@router.get("/backfill/{job_id}/progress", response_model=ApiResponse[BackfillJobEntry])
async def get_backfill_job_progress(
    job_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BackfillJobEntry]:
    """Return progress metrics for a single backfill job.

    Returns the same full job detail as GET /backfill/{job_id} — the caller
    can read rows_processed, rows_skipped, cost_spent_cents, status, and cursor
    to render a live progress view.

    Returns 404 if the job does not exist.
    Returns 503 on database error.
    """
    return await get_backfill_job(job_id=job_id, db=db)


# ---------------------------------------------------------------------------
# Helpers — triage rules
# ---------------------------------------------------------------------------


def _row_to_triage_rule(r: Any) -> TriageRule:
    """Convert an asyncpg row to a TriageRule model."""
    condition = r["condition"]
    if isinstance(condition, str):
        condition = json.loads(condition)
    return TriageRule(
        id=str(r["id"]),
        rule_type=r["rule_type"],
        condition=condition,
        action=r["action"],
        priority=r["priority"],
        enabled=r["enabled"],
        created_by=r["created_by"],
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


async def _assert_route_to_eligible(pool: Any, action: str) -> None:
    """Validate that a route_to:<butler> action references a registered butler.

    Raises HTTPException 422 when the target is not found in butler_registry.
    No-op for non-route_to actions.
    """
    if not action.startswith("route_to:"):
        return
    target = action[len("route_to:") :]
    row = await pool.fetchrow("SELECT name FROM butler_registry WHERE name = $1", target)
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"route_to target '{target}' is not a registered butler",
        )


# ---------------------------------------------------------------------------
# GET /triage-rules — list rules
# ---------------------------------------------------------------------------


@router.get("/triage-rules", response_model=ApiResponse[list[TriageRule]])
async def list_triage_rules(
    rule_type: str | None = Query(None, description="Filter by rule_type"),
    enabled: bool | None = Query(None, description="Filter by enabled state"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[TriageRule]]:
    """List active (non-deleted) triage rules with optional filters.

    Results are ordered by priority ASC, created_at ASC, id ASC per spec §5.4.
    """
    pool = _pool(db)

    conditions = ["deleted_at IS NULL"]
    args: list[Any] = []
    idx = 1

    if rule_type is not None:
        conditions.append(f"rule_type = ${idx}")
        args.append(rule_type)
        idx += 1

    if enabled is not None:
        conditions.append(f"enabled = ${idx}")
        args.append(enabled)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    rows = await pool.fetch(
        f"SELECT id, rule_type, condition, action, priority, enabled,"
        f" created_by, created_at, updated_at"
        f" FROM triage_rules{where}"
        f" ORDER BY priority ASC, created_at ASC, id ASC",
        *args,
    )

    total = len(rows)
    data = [_row_to_triage_rule(r) for r in rows]

    from butlers.api.models import ApiMeta

    return ApiResponse[list[TriageRule]](data=data, meta=ApiMeta(total=total))


# ---------------------------------------------------------------------------
# POST /triage-rules — create rule
# ---------------------------------------------------------------------------


@router.post("/triage-rules", response_model=ApiResponse[TriageRule], status_code=201)
async def create_triage_rule(
    body: TriageRuleCreate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TriageRule]:
    """Create a new triage rule.

    Validates rule_type/condition schema compatibility and route_to target
    eligibility before writing to the database.  The new rule is marked
    ``created_by='dashboard'``.
    """
    pool = _pool(db)

    # Validate route_to eligibility against the registry
    await _assert_route_to_eligible(pool, body.action)

    # Re-validate and normalise condition (also done by Pydantic on body parse)
    try:
        validated_condition = validate_condition(body.rule_type, body.condition)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    row = await pool.fetchrow(
        "INSERT INTO triage_rules"
        " (rule_type, condition, action, priority, enabled, created_by)"
        " VALUES ($1, $2::jsonb, $3, $4, $5, 'dashboard')"
        " RETURNING id, rule_type, condition, action, priority, enabled,"
        "           created_by, created_at, updated_at",
        body.rule_type,
        json.dumps(validated_condition),
        body.action,
        body.priority,
        body.enabled,
    )

    return ApiResponse[TriageRule](data=_row_to_triage_rule(row))


# ---------------------------------------------------------------------------
# PATCH /triage-rules/{rule_id} — update rule
# ---------------------------------------------------------------------------


@router.patch("/triage-rules/{rule_id}", response_model=ApiResponse[TriageRule])
async def update_triage_rule(
    rule_id: str,
    body: TriageRuleUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TriageRule]:
    """Partially update a triage rule.

    Supports partial fields: condition, action, priority, enabled.
    Returns 404 when the rule does not exist or has been soft-deleted.
    """
    pool = _pool(db)

    # Validate UUID format
    try:
        UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="rule_id must be a valid UUID")

    # Fetch the existing rule to validate condition+rule_type compatibility
    existing = await pool.fetchrow(
        "SELECT id, rule_type, condition, action, priority, enabled,"
        " created_by, created_at, updated_at"
        " FROM triage_rules WHERE id = $1 AND deleted_at IS NULL",
        rule_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Triage rule '{rule_id}' not found")

    # Build updated fields
    updates: dict[str, Any] = {}

    if body.action is not None:
        await _assert_route_to_eligible(pool, body.action)
        updates["action"] = body.action

    if body.condition is not None:
        # We need the rule_type to validate the condition
        rule_type = existing["rule_type"]
        try:
            validated_condition = validate_condition(rule_type, body.condition)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        updates["condition"] = json.dumps(validated_condition)

    if body.priority is not None:
        updates["priority"] = body.priority

    if body.enabled is not None:
        updates["enabled"] = body.enabled

    if not updates:
        # No fields to update — return existing rule unchanged
        return ApiResponse[TriageRule](data=_row_to_triage_rule(existing))

    # Build SET clause
    set_parts: list[str] = []
    args: list[Any] = []
    idx = 1

    for field, value in updates.items():
        if field == "condition":
            set_parts.append(f"condition = ${idx}::jsonb")
        else:
            set_parts.append(f"{field} = ${idx}")
        args.append(value)
        idx += 1

    set_parts.append(f"updated_at = ${idx}")
    args.append(datetime.datetime.now(datetime.UTC))
    idx += 1

    args.append(rule_id)

    row = await pool.fetchrow(
        f"UPDATE triage_rules SET {', '.join(set_parts)}"
        f" WHERE id = ${idx} AND deleted_at IS NULL"
        f" RETURNING id, rule_type, condition, action, priority, enabled,"
        f"           created_by, created_at, updated_at",
        *args,
    )

    if row is None:
        raise HTTPException(status_code=404, detail=f"Triage rule '{rule_id}' not found")

    return ApiResponse[TriageRule](data=_row_to_triage_rule(row))


# ---------------------------------------------------------------------------
# DELETE /triage-rules/{rule_id} — soft-delete rule
# ---------------------------------------------------------------------------


@router.delete("/triage-rules/{rule_id}", status_code=204)
async def delete_triage_rule(
    rule_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete a triage rule.

    Sets ``deleted_at=NOW()`` and ``enabled=FALSE``.
    Returns 204 No Content on success.
    Returns 404 when the rule does not exist or is already deleted.
    """
    pool = _pool(db)

    # Validate UUID format
    try:
        UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="rule_id must be a valid UUID")

    now = datetime.datetime.now(datetime.UTC)
    result = await pool.execute(
        "UPDATE triage_rules"
        " SET deleted_at = $1, enabled = FALSE, updated_at = $1"
        " WHERE id = $2 AND deleted_at IS NULL",
        now,
        rule_id,
    )

    rows_affected = int(result.split(" ")[-1]) if result else 0
    if rows_affected == 0:
        raise HTTPException(status_code=404, detail=f"Triage rule '{rule_id}' not found")


# ---------------------------------------------------------------------------
# Triage evaluator — pure Python, mirrors spec §5.4 logic
# ---------------------------------------------------------------------------


def _evaluate_rule(rule: TriageRuleCreate, envelope: Any) -> tuple[bool, str]:
    """Evaluate a single triage rule against a sample envelope.

    Returns ``(matched, reason)`` — a boolean and a human-readable reason string.

    This is the canonical evaluator used by the /test dry-run endpoint. It mirrors
    the deterministic match logic defined in spec §5.4 and §4.2.
    """
    rule_type = rule.rule_type
    condition = rule.condition

    if rule_type == "sender_domain":
        sender_address = envelope.sender.identity.lower()
        # Extract domain from "local@domain" or treat whole string as domain
        domain_part = sender_address.split("@")[-1] if "@" in sender_address else sender_address
        target_domain = condition["domain"].lower()
        match_mode = condition.get("match", "exact")

        if match_mode == "exact":
            matched = domain_part == target_domain
            reason = (
                f"sender domain exact match: {domain_part} == {target_domain}"
                if matched
                else f"sender domain {domain_part!r} does not exactly match {target_domain!r}"
            )
        else:  # suffix
            matched = domain_part == target_domain or domain_part.endswith("." + target_domain)
            reason = (
                f"sender domain suffix match: {domain_part} matches *.{target_domain}"
                if matched
                else f"sender domain {domain_part!r} does not suffix-match {target_domain!r}"
            )
        return matched, reason

    elif rule_type == "sender_address":
        sender = envelope.sender.identity.lower()
        target = condition["address"].lower()
        matched = sender == target
        reason = (
            f"sender address exact match: {sender}"
            if matched
            else f"sender address {sender!r} does not match {target!r}"
        )
        return matched, reason

    elif rule_type == "header_condition":
        headers = {k.lower(): v for k, v in (envelope.payload.headers or {}).items()}
        header_name = condition["header"].lower()
        op = condition["op"]
        value = condition.get("value")

        if op == "present":
            matched = header_name in headers
            reason = (
                f"header {condition['header']!r} is present"
                if matched
                else f"header {condition['header']!r} is not present"
            )
        elif op == "equals":
            actual = headers.get(header_name)
            matched = actual is not None and actual == value
            reason = (
                f"header {condition['header']!r} equals {value!r}"
                if matched
                else f"header {condition['header']!r} value {actual!r} does not equal {value!r}"
            )
        elif op == "contains":
            actual = headers.get(header_name)
            matched = actual is not None and value is not None and value in actual
            reason = (
                f"header {condition['header']!r} contains {value!r}"
                if matched
                else f"header {condition['header']!r} value {actual!r} does not contain {value!r}"
            )
        else:
            matched = False
            reason = f"unknown op {op!r}"
        return matched, reason

    elif rule_type == "mime_type":
        mime_parts = envelope.payload.mime_parts or []
        target_type = condition["type"].lower()

        def _type_matches(part_type: str) -> bool:
            part_type = part_type.lower()
            if target_type.endswith("/*"):
                # Wildcard subtype: match major type
                major = target_type[:-2]
                return part_type == major or part_type.startswith(major + "/")
            return part_type == target_type

        matched = any(_type_matches(p.get("type", "")) for p in mime_parts)
        reason = (
            f"MIME part matches {target_type!r}"
            if matched
            else f"no MIME part matches {target_type!r}"
        )
        return matched, reason

    return False, f"unknown rule_type {rule_type!r}"


# ---------------------------------------------------------------------------
# POST /triage-rules/test — dry-run evaluation
# ---------------------------------------------------------------------------


@router.post("/triage-rules/test", response_model=TriageRuleTestResponse)
async def test_triage_rule(
    body: TriageRuleTestRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> TriageRuleTestResponse:
    """Dry-run evaluation of a triage rule against a sample envelope.

    Uses the same evaluator as the production triage path.
    Does NOT write any routing or inbox state.
    """
    # Pool must be reachable (validates DB connectivity) but we don't query in evaluator
    _pool(db)

    rule = body.rule
    envelope = body.envelope

    matched, reason = _evaluate_rule(rule, envelope)

    if matched:
        action = rule.action
        if action.startswith("route_to:"):
            decision = "route_to"
            target_butler = action[len("route_to:") :]
        else:
            decision = action
            target_butler = None

        result = TriageRuleTestResult(
            matched=True,
            decision=decision,
            target_butler=target_butler,
            matched_rule_type=rule.rule_type,
            reason=reason,
        )
    else:
        result = TriageRuleTestResult(
            matched=False,
            decision=None,
            target_butler=None,
            matched_rule_type=None,
            reason=reason,
        )

    return TriageRuleTestResponse(data=result)


# ---------------------------------------------------------------------------
# Thread affinity settings endpoints
# ---------------------------------------------------------------------------


@router.get("/thread-affinity/settings", response_model=ThreadAffinitySettings)
async def get_thread_affinity_settings(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ThreadAffinitySettings:
    """Get current global thread-affinity routing settings.

    Returns the singleton settings row with global enable/disable toggle,
    TTL days, and per-thread override map.
    """
    pool = _pool(db)

    row = await pool.fetchrow(
        """
        SELECT
            thread_affinity_enabled,
            thread_affinity_ttl_days,
            thread_overrides,
            updated_at::text AS updated_at
        FROM thread_affinity_settings
        WHERE id = 1
        """
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Thread affinity settings row not found")

    overrides_raw = row["thread_overrides"]
    if isinstance(overrides_raw, dict):
        overrides = overrides_raw
    else:
        overrides = {}

    return ThreadAffinitySettings(
        enabled=bool(row["thread_affinity_enabled"]),
        ttl_days=int(row["thread_affinity_ttl_days"]),
        thread_overrides=overrides,
        updated_at=row["updated_at"],
    )


@router.patch("/thread-affinity/settings", response_model=ThreadAffinitySettings)
async def update_thread_affinity_settings(
    body: ThreadAffinitySettingsUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ThreadAffinitySettings:
    """Update global thread-affinity routing settings.

    Partial update: only fields provided in the request body are changed.
    The singleton row (id=1) is created if it does not exist.
    """
    pool = _pool(db)

    if body.enabled is None and body.ttl_days is None:
        raise HTTPException(status_code=422, detail="No fields provided for update")

    if body.ttl_days is not None and body.ttl_days <= 0:
        raise HTTPException(status_code=422, detail="ttl_days must be a positive integer")

    # Build SET clauses
    set_clauses: list[str] = ["updated_at = NOW()"]
    args: list = []

    if body.enabled is not None:
        args.append(body.enabled)
        set_clauses.append(f"thread_affinity_enabled = ${len(args)}")

    if body.ttl_days is not None:
        args.append(body.ttl_days)
        set_clauses.append(f"thread_affinity_ttl_days = ${len(args)}")

    set_sql = ", ".join(set_clauses)

    await pool.execute(
        f"""
        INSERT INTO thread_affinity_settings (id, thread_affinity_enabled, thread_affinity_ttl_days)
        VALUES (1, TRUE, 30)
        ON CONFLICT (id) DO UPDATE
        SET {set_sql}
        """,
        *args,
    )

    # Return updated row
    return await get_thread_affinity_settings(db=db)


@router.get("/thread-affinity/overrides", response_model=list[ThreadOverrideEntry])
async def list_thread_affinity_overrides(
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[ThreadOverrideEntry]:
    """List all per-thread affinity overrides.

    Returns a list of thread_id → mode pairs from the overrides JSONB column.
    """
    pool = _pool(db)

    row = await pool.fetchrow("SELECT thread_overrides FROM thread_affinity_settings WHERE id = 1")

    if row is None or not row["thread_overrides"]:
        return []

    overrides_raw = row["thread_overrides"]
    if not isinstance(overrides_raw, dict):
        return []

    return [ThreadOverrideEntry(thread_id=tid, mode=mode) for tid, mode in overrides_raw.items()]


@router.put(
    "/thread-affinity/overrides/{thread_id}",
    response_model=ThreadOverrideEntry,
    status_code=200,
)
async def upsert_thread_affinity_override(
    thread_id: str,
    body: ThreadOverrideUpsert,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ThreadOverrideEntry:
    """Create or update a per-thread affinity override.

    Set mode='disabled' to suppress affinity for a thread.
    Set mode='force:<butler>' to always route this thread to a specific butler.
    """
    pool = _pool(db)

    if not thread_id or not thread_id.strip():
        raise HTTPException(status_code=422, detail="thread_id must not be empty")

    clean_thread_id = thread_id.strip()

    # Upsert the settings row if missing, then merge override into JSONB
    await pool.execute(
        """
        INSERT INTO thread_affinity_settings (id)
        VALUES (1)
        ON CONFLICT (id) DO NOTHING
        """
    )

    await pool.execute(
        """
        UPDATE thread_affinity_settings
        SET thread_overrides = thread_overrides || jsonb_build_object($1::text, $2::text),
            updated_at = NOW()
        WHERE id = 1
        """,
        clean_thread_id,
        body.mode,
    )

    return ThreadOverrideEntry(thread_id=clean_thread_id, mode=body.mode)


@router.delete(
    "/thread-affinity/overrides/{thread_id}",
    status_code=204,
)
async def delete_thread_affinity_override(
    thread_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Delete a per-thread affinity override.

    After deletion, the thread will use history-based affinity lookup (or global settings).
    """
    pool = _pool(db)

    if not thread_id or not thread_id.strip():
        raise HTTPException(status_code=422, detail="thread_id must not be empty")

    clean_thread_id = thread_id.strip()

    await pool.execute(
        """
        UPDATE thread_affinity_settings
        SET thread_overrides = thread_overrides - $1::text,
            updated_at = NOW()
        WHERE id = 1
        """,
        clean_thread_id,
    )


# ---------------------------------------------------------------------------
# Routing instructions — owner-defined routing directives
# ---------------------------------------------------------------------------


def _row_to_routing_instruction(row: Any) -> RoutingInstruction:
    """Convert an asyncpg Record to a RoutingInstruction model."""
    return RoutingInstruction(
        id=str(row["id"]),
        instruction=row["instruction"],
        priority=row["priority"],
        enabled=row["enabled"],
        created_by=row["created_by"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


# ---------------------------------------------------------------------------
# GET /routing-instructions — list instructions
# ---------------------------------------------------------------------------


@router.get("/routing-instructions", response_model=ApiResponse[list[RoutingInstruction]])
async def list_routing_instructions(
    enabled: bool | None = Query(None, description="Filter by enabled state"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[RoutingInstruction]]:
    """List active (non-deleted) routing instructions.

    Results are ordered by priority ASC, created_at ASC for deterministic
    prompt injection ordering.
    """
    pool = _pool(db)

    conditions = ["deleted_at IS NULL"]
    args: list[Any] = []
    idx = 1

    if enabled is not None:
        conditions.append(f"enabled = ${idx}")
        args.append(enabled)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    rows = await pool.fetch(
        f"SELECT id, instruction, priority, enabled,"
        f" created_by, created_at, updated_at"
        f" FROM routing_instructions{where}"
        f" ORDER BY priority ASC, created_at ASC, id ASC",
        *args,
    )

    from butlers.api.models import ApiMeta

    return ApiResponse[list[RoutingInstruction]](
        data=[_row_to_routing_instruction(r) for r in rows],
        meta=ApiMeta(total=len(rows)),
    )


# ---------------------------------------------------------------------------
# POST /routing-instructions — create instruction
# ---------------------------------------------------------------------------


@router.post(
    "/routing-instructions",
    response_model=ApiResponse[RoutingInstruction],
    status_code=201,
)
async def create_routing_instruction(
    body: RoutingInstructionCreate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[RoutingInstruction]:
    """Create a new routing instruction."""
    pool = _pool(db)

    row = await pool.fetchrow(
        "INSERT INTO routing_instructions"
        " (instruction, priority, enabled, created_by)"
        " VALUES ($1, $2, $3, 'dashboard')"
        " RETURNING id, instruction, priority, enabled,"
        "           created_by, created_at, updated_at",
        body.instruction,
        body.priority,
        body.enabled,
    )

    return ApiResponse[RoutingInstruction](data=_row_to_routing_instruction(row))


# ---------------------------------------------------------------------------
# PATCH /routing-instructions/{instruction_id} — update instruction
# ---------------------------------------------------------------------------


@router.patch(
    "/routing-instructions/{instruction_id}",
    response_model=ApiResponse[RoutingInstruction],
)
async def update_routing_instruction(
    instruction_id: str,
    body: RoutingInstructionUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[RoutingInstruction]:
    """Partially update a routing instruction.

    Supports partial fields: instruction, priority, enabled.
    Returns 404 when the instruction does not exist or has been soft-deleted.
    """
    pool = _pool(db)

    try:
        UUID(instruction_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="instruction_id must be a valid UUID")

    existing = await pool.fetchrow(
        "SELECT id FROM routing_instructions WHERE id = $1 AND deleted_at IS NULL",
        instruction_id,
    )
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Routing instruction '{instruction_id}' not found",
        )

    updates: dict[str, Any] = {}
    if body.instruction is not None:
        updates["instruction"] = body.instruction
    if body.priority is not None:
        updates["priority"] = body.priority
    if body.enabled is not None:
        updates["enabled"] = body.enabled

    if not updates:
        row = await pool.fetchrow(
            "SELECT id, instruction, priority, enabled,"
            " created_by, created_at, updated_at"
            " FROM routing_instructions WHERE id = $1",
            instruction_id,
        )
        return ApiResponse[RoutingInstruction](data=_row_to_routing_instruction(row))

    set_parts: list[str] = []
    args: list[Any] = []
    idx = 1

    for col, value in updates.items():
        set_parts.append(f"{col} = ${idx}")
        args.append(value)
        idx += 1

    set_parts.append(f"updated_at = ${idx}")
    args.append(datetime.datetime.now(datetime.UTC))
    idx += 1

    args.append(instruction_id)

    row = await pool.fetchrow(
        f"UPDATE routing_instructions SET {', '.join(set_parts)}"
        f" WHERE id = ${idx} AND deleted_at IS NULL"
        f" RETURNING id, instruction, priority, enabled,"
        f"           created_by, created_at, updated_at",
        *args,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Routing instruction '{instruction_id}' not found",
        )

    return ApiResponse[RoutingInstruction](data=_row_to_routing_instruction(row))


# ---------------------------------------------------------------------------
# DELETE /routing-instructions/{instruction_id} — soft-delete instruction
# ---------------------------------------------------------------------------


@router.delete("/routing-instructions/{instruction_id}", status_code=204)
async def delete_routing_instruction(
    instruction_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-delete a routing instruction.

    Sets ``deleted_at=NOW()`` and ``enabled=FALSE``.
    Returns 204 No Content on success.
    Returns 404 when the instruction does not exist or is already deleted.
    """
    pool = _pool(db)

    try:
        UUID(instruction_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="instruction_id must be a valid UUID")

    now = datetime.datetime.now(datetime.UTC)
    result = await pool.execute(
        "UPDATE routing_instructions"
        " SET deleted_at = $1, enabled = FALSE, updated_at = $1"
        " WHERE id = $2 AND deleted_at IS NULL",
        now,
        instruction_id,
    )

    rows_affected = int(result.split(" ")[-1]) if result else 0
    if rows_affected == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Routing instruction '{instruction_id}' not found",
        )
