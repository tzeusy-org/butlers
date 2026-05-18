"""Connector lifecycle endpoints — per-connector lifecycle actions for the ingestion console.

Provides:

- ``router`` — endpoints under ``/api/ingestion/connectors``

Endpoints
---------
POST /api/ingestion/connectors/{type}/{identity}/pause    — pause a connector (audit-only)
POST /api/ingestion/connectors/{type}/{identity}/run-now  — resume a paused connector (audit-only)

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
      connector-lifecycle-ceremony/spec.md
§Requirement: Per-action lifecycle gate matrix
§Requirement: Run-now semantics
§Requirement: Audit emission for all lifecycle actions
"""

from __future__ import annotations

import logging

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
