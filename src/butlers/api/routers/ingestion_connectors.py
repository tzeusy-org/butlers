"""Connector endpoints for the ingestion console.

Provides:

- ``router`` — endpoints under ``/api/ingestion/connectors``

Endpoints
---------
GET  /api/ingestion/connectors/summaries        — connector list with aggregates_available flag
GET  /api/ingestion/connectors/cross-summary    — cross-connector aggregate + aggregates_available
POST /api/ingestion/connectors/{type}/{identity}/pause    — pause a connector (audit-only)
POST /api/ingestion/connectors/{type}/{identity}/run-now  — resume a paused connector (audit-only)

The ``summaries`` and ``cross-summary`` endpoints proxy the existing
``/api/switchboard/connectors`` and ``/api/switchboard/connectors/summary``
endpoints and add the ``aggregates_available`` flag derived from whether the
Prometheus backend is reachable (via the pipeline stats cache).

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
      connector-lifecycle-ceremony/spec.md
      connector-state-aggregates/spec.md (aggregates_available threading)
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers.audit import append as _audit_append

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/connectors", tags=["ingestion"])

_SWITCHBOARD_BUTLER = "switchboard"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the switchboard butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    Connector lifecycle state is stored in connector_registry, which lives
    in the switchboard schema.
    """
    try:
        return db.pool(_SWITCHBOARD_BUTLER)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Connector registry database is not available",
        )


def _get_prometheus_url() -> str | None:
    """Return Prometheus base URL from env, or None if not configured or empty."""
    return os.environ.get("PROMETHEUS_URL") or None


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/summaries
# ---------------------------------------------------------------------------


@router.get("/summaries", response_model=ApiResponse[dict])
async def list_connector_summaries_with_aggregates(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Return the connector list with an ``aggregates_available`` flag.

    Fetches connector registry rows from the switchboard database and
    augments the response with ``aggregates_available`` indicating whether
    Prometheus-backed metrics (spark24h, rate1h, etc.) are expected to be
    valid.

    ``aggregates_available`` is ``true`` when ``PROMETHEUS_URL`` is configured
    and the last pipeline cache entry was successful; ``false`` otherwise.

    Always returns HTTP 200 — database errors fall back to an empty list.
    """
    pool = _pool(db)
    aggregates_available = _get_prometheus_url() is not None

    # Check the pipeline cache for a recent successful fetch
    try:
        import time

        from butlers.api.routers.ingestion_pipeline import _CACHE_TTL_SECONDS, _pipeline_cache

        cached = _pipeline_cache.get("24h")
        if cached is not None:
            ts, data = cached
            if time.monotonic() - ts < _CACHE_TTL_SECONDS:
                aggregates_available = data.get("aggregates_available", False)
    except Exception:
        # Cache read failure is non-fatal
        pass

    try:
        rows = await pool.fetch(
            """
            SELECT
                connector_type,
                endpoint_identity,
                state,
                error_message,
                version,
                uptime_s,
                last_heartbeat_at,
                first_seen_at,
                counter_messages_ingested,
                counter_messages_failed
            FROM connector_registry
            WHERE deleted_at IS NULL
            ORDER BY first_seen_at DESC
            """,
        )
    except Exception:
        logger.warning("connector summaries: failed to fetch from registry", exc_info=True)
        return ApiResponse[dict](
            data={"connectors": [], "aggregates_available": aggregates_available}
        )

    import datetime as dt

    def _liveness(last_heartbeat_at: dt.datetime | None) -> str:
        if last_heartbeat_at is None:
            return "offline"
        now = dt.datetime.now(dt.UTC)
        age = (now - last_heartbeat_at).total_seconds()
        if age < -300:
            return "offline"
        elif age <= 300:
            return "online"
        elif age <= 900:
            return "stale"
        return "offline"

    connectors = []
    for r in rows:
        liveness = _liveness(r["last_heartbeat_at"])
        connectors.append(
            {
                "connector_type": r["connector_type"],
                "endpoint_identity": r["endpoint_identity"],
                "liveness": liveness,
                "state": r["state"],
                "error_message": r["error_message"],
                "version": r["version"],
                "uptime_s": r["uptime_s"],
                "last_heartbeat_at": (
                    r["last_heartbeat_at"].isoformat() if r["last_heartbeat_at"] else None
                ),
                "first_seen_at": r["first_seen_at"].isoformat(),
                "today": {
                    "messages_ingested": r["counter_messages_ingested"] or 0,
                    "messages_failed": r["counter_messages_failed"] or 0,
                },
            }
        )

    return ApiResponse[dict](
        data={"connectors": connectors, "aggregates_available": aggregates_available}
    )


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/cross-summary
# ---------------------------------------------------------------------------


@router.get("/cross-summary", response_model=ApiResponse[dict])
async def get_cross_connector_summary_with_aggregates(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Return cross-connector aggregate summary with ``aggregates_available`` flag.

    Aggregates health and volume counts across all active connectors and
    includes ``aggregates_available`` indicating whether Prometheus-backed
    per-connector time-series metrics are expected to be valid.

    Always returns HTTP 200 — database errors fall back to zero-value summary.
    """
    pool = _pool(db)
    aggregates_available = _get_prometheus_url() is not None

    # Check the pipeline cache for a recent successful fetch
    try:
        import time

        from butlers.api.routers.ingestion_pipeline import _CACHE_TTL_SECONDS, _pipeline_cache

        cached = _pipeline_cache.get("24h")
        if cached is not None:
            ts, data = cached
            if time.monotonic() - ts < _CACHE_TTL_SECONDS:
                aggregates_available = data.get("aggregates_available", False)
    except Exception:
        pass

    try:
        row = await pool.fetchrow(
            """
            SELECT
                count(*) AS total_connectors,
                count(*) FILTER (WHERE state = 'healthy') AS online_count,
                count(*) FILTER (WHERE state = 'degraded') AS stale_count,
                count(*) FILTER (WHERE state = 'error') AS offline_count,
                coalesce(sum(counter_messages_ingested), 0) AS total_messages_ingested,
                coalesce(sum(counter_messages_failed), 0) AS total_messages_failed
            FROM connector_registry
            WHERE deleted_at IS NULL
            """,
        )
    except Exception:
        logger.warning("cross-summary: failed to query connector_registry", exc_info=True)
        return ApiResponse[dict](
            data={
                "total_connectors": 0,
                "connectors_online": 0,
                "connectors_stale": 0,
                "connectors_offline": 0,
                "total_messages_ingested": 0,
                "total_messages_failed": 0,
                "overall_error_rate_pct": 0.0,
                "aggregates_available": aggregates_available,
            }
        )

    if row is None:
        return ApiResponse[dict](
            data={
                "total_connectors": 0,
                "connectors_online": 0,
                "connectors_stale": 0,
                "connectors_offline": 0,
                "total_messages_ingested": 0,
                "total_messages_failed": 0,
                "overall_error_rate_pct": 0.0,
                "aggregates_available": aggregates_available,
            }
        )

    total_ingested = int(row["total_messages_ingested"] or 0)
    total_failed = int(row["total_messages_failed"] or 0)
    total_attempts = total_ingested + total_failed
    error_rate_pct = (total_failed / total_attempts * 100.0) if total_attempts > 0 else 0.0

    return ApiResponse[dict](
        data={
            "total_connectors": int(row["total_connectors"] or 0),
            "connectors_online": int(row["online_count"] or 0),
            "connectors_stale": int(row["stale_count"] or 0),
            "connectors_offline": int(row["offline_count"] or 0),
            "total_messages_ingested": total_ingested,
            "total_messages_failed": total_failed,
            "overall_error_rate_pct": round(error_rate_pct, 2),
            "aggregates_available": aggregates_available,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/pause
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/pause",
    response_model=ApiResponse[dict],
    status_code=200,
)
async def pause_connector(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Pause a connector — audit-only, no Approvals gate.

    Sets ``connector_registry.state`` to ``'paused'`` and emits an audit
    entry with ``action='connector.pause'``.

    No Approvals module call is made (this action is audit-log-only per the
    lifecycle gate matrix spec).

    Returns HTTP 200 with the connector identity on success.
    Returns HTTP 404 if the connector is not found in the registry.
    Returns HTTP 503 if the connector registry is unavailable.
    """
    pool = _pool(db)

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    "UPDATE connector_registry"
                    " SET state = 'paused'"
                    " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL"
                    " RETURNING connector_type, endpoint_identity, state",
                    connector_type,
                    endpoint_identity,
                )
            except Exception:
                logger.warning(
                    "Failed to pause connector %s/%s",
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

            # Emit audit entry within the same transaction — atomicity with the state change
            try:
                client_host = getattr(request.client, "host", None) if request.client else None
                await _audit_append(
                    conn,
                    actor="dashboard",
                    action="connector.pause",
                    target=f"{connector_type}/{endpoint_identity}",
                    note=f"Connector '{connector_type}/{endpoint_identity}' paused via dashboard",
                    ip=client_host,
                )
            except Exception:
                logger.warning(
                    "ingestion_connectors: failed to append audit_log entry for pause %s/%s",
                    connector_type,
                    endpoint_identity,
                    exc_info=True,
                )

    logger.info("Paused connector %s/%s", connector_type, endpoint_identity)

    return ApiResponse[dict](
        data={
            "connector_type": str(row["connector_type"]),
            "endpoint_identity": str(row["endpoint_identity"]),
            "state": str(row["state"]),
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/run-now
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/run-now",
    response_model=ApiResponse[dict],
    status_code=200,
)
async def run_now_connector(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Resume a paused connector and trigger the next poll cycle — audit-only, no Approvals gate.

    Validates that the connector is currently in the ``'paused'`` state.
    Returns HTTP 409 if the connector is not paused (spec: "Run-now semantics").

    On a paused connector:
    - Clears the pause by setting ``state`` back to ``'unknown'`` (the connector
      will self-report its true state on the next heartbeat)
    - Emits an audit entry with ``action='connector.run_now'``

    The connector picks up the state change on its next poll cycle.

    Returns HTTP 200 with the connector identity on success.
    Returns HTTP 404 if the connector is not found in the registry.
    Returns HTTP 409 if the connector is not currently paused.
    Returns HTTP 503 if the connector registry is unavailable.
    """
    pool = _pool(db)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # SELECT FOR UPDATE: lock the row to prevent a concurrent pause/run-now race
            try:
                current_row = await conn.fetchrow(
                    "SELECT connector_type, endpoint_identity, state"
                    " FROM connector_registry"
                    " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL"
                    " FOR UPDATE",
                    connector_type,
                    endpoint_identity,
                )
            except Exception:
                logger.warning(
                    "Failed to fetch connector state for run-now %s/%s",
                    connector_type,
                    endpoint_identity,
                    exc_info=True,
                )
                raise HTTPException(status_code=503, detail="Connector registry is not available")

            if current_row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
                )

            current_state = str(current_row["state"])
            if current_state != "paused":
                # Spec: "Run-now on non-paused connector rejected"
                # The response body identifies the connector's actual state.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Connector '{connector_type}/{endpoint_identity}' is not paused "
                        f"(current state: '{current_state}'). "
                        "run-now is only valid on a paused connector."
                    ),
                )

            # Clear the pause — set state to 'unknown'; connector self-reports on next heartbeat
            try:
                row = await conn.fetchrow(
                    "UPDATE connector_registry"
                    " SET state = 'unknown'"
                    " WHERE connector_type = $1 AND endpoint_identity = $2"
                    " RETURNING connector_type, endpoint_identity, state",
                    connector_type,
                    endpoint_identity,
                )
            except Exception:
                logger.warning(
                    "Failed to clear pause for connector %s/%s",
                    connector_type,
                    endpoint_identity,
                    exc_info=True,
                )
                raise HTTPException(status_code=503, detail="Connector registry is not available")

            # Emit audit entry within the same transaction — atomicity with the state change
            try:
                client_host = getattr(request.client, "host", None) if request.client else None
                await _audit_append(
                    conn,
                    actor="dashboard",
                    action="connector.run_now",
                    target=f"{connector_type}/{endpoint_identity}",
                    note=f"Connector '{connector_type}/{endpoint_identity}' resumed via run-now",
                    ip=client_host,
                )
            except Exception:
                logger.warning(
                    "ingestion_connectors: failed to append audit_log entry for run-now %s/%s",
                    connector_type,
                    endpoint_identity,
                    exc_info=True,
                )

    logger.info("run-now: cleared pause for connector %s/%s", connector_type, endpoint_identity)

    return ApiResponse[dict](
        data={
            "connector_type": str(row["connector_type"]),
            "endpoint_identity": str(row["endpoint_identity"]),
            "state": str(row["state"]),
        }
    )
