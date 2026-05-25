"""Connector endpoints for the ingestion console.

Provides:

- ``router`` — endpoints under ``/api/ingestion/connectors``

Endpoints
---------
GET  /api/ingestion/connectors/summaries        — connector list with aggregates_available flag
GET  /api/ingestion/connectors/cross-summary    — cross-connector aggregate + aggregates_available
POST /api/ingestion/connectors/{type}/{identity}/pause       — pause a connector (audit-only)
POST /api/ingestion/connectors/{type}/{identity}/run-now    — resume a paused connector (audit-only)
POST /api/ingestion/connectors/{type}/{identity}/disconnect — Approvals-gated; soft-delete (§4.4)
POST /api/ingestion/connectors/{type}/{identity}/rotate-token — Approvals-gated; masked (§4.5)
POST /api/ingestion/connectors/{type}/{identity}/reauth      — BLOCKED HTTP 503 (§4.6)
GET  /api/ingestion/connectors/available                     — enumerable connector profiles
GET  /api/ingestion/connectors/{type}/{identity}/events      — recent events [bu-5ywn2]
GET  /api/ingestion/connectors/{type}/{identity}/incidents   — incident events [bu-5ywn2]
GET  /api/ingestion/connectors/{type}/{identity}/routing-rules — scoped rules [bu-5ywn2]

The ``summaries`` and ``cross-summary`` endpoints proxy the existing
``/api/switchboard/connectors`` and ``/api/switchboard/connectors/summary``
endpoints and add the ``aggregates_available`` flag derived from whether the
Prometheus backend is reachable (via the pipeline stats cache).

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
      connector-lifecycle-ceremony/spec.md
      connector-state-aggregates/spec.md (aggregates_available threading)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

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


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/disconnect
# ---------------------------------------------------------------------------

#: Path to the connector-oauth-scope-surface spec (blocking reauth)
_OAUTH_SCOPE_SURFACE_SPEC = "connector-oauth-scope-surface"

#: Known token/credential pattern prefixes used by the masking test
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "token",
        "credential",
        "secret",
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "oauth_token",
        "new_token",
        "new_credential",
    }
)


@router.post(
    "/{connector_type}/{endpoint_identity}/disconnect",
    response_model=ApiResponse[dict],
    status_code=202,
)
async def disconnect_connector(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Disconnect a connector — Approvals-gated; soft-deletes via ``deleted_at`` (§4.4).

    Submits a pending approval action for the disconnect operation.  The
    connector row is NOT immediately modified; the approval gate keeps the
    connector in its current state until the action is resolved.

    When the approval resolves:
    - Approved: ``connector_registry.deleted_at`` is set to NOW() (soft-delete)
    - Denied: no state change occurs

    The Approvals module runs at the MCP server level — the dashboard API
    submits the intent via ``pending_actions``; the MCP layer resolves it.

    Returns HTTP 202 with ``{status: "pending_approval", action_id: ...}`` on success.
    Returns HTTP 404 if the connector is not found in the registry.
    Returns HTTP 503 if the connector registry or approvals subsystem is unavailable.

    An audit entry with ``action='connector.disconnect'`` is emitted on submission.
    """
    pool = _pool(db)

    # Verify connector exists before creating a pending action
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "disconnect: failed to fetch connector %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    # Create a pending_actions row for the Approvals gate
    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    target = f"{connector_type}/{endpoint_identity}"
    tool_args = {"connector_type": connector_type, "endpoint_identity": endpoint_identity}

    # 72-hour expiry for lifecycle approval actions
    expires_at = now + timedelta(hours=72)

    try:
        await pool.execute(
            "INSERT INTO pending_actions"
            " (id, tool_name, tool_args, agent_summary, status, requested_at, expires_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            action_id,
            "connector_disconnect",
            json.dumps(tool_args),
            f"Disconnect connector '{target}' (soft-delete)",
            "pending",
            now,
            expires_at,
        )
    except Exception:
        logger.warning(
            "disconnect: failed to insert pending_action for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Approvals subsystem is not available")

    # Emit audit entry for the disconnect submission
    try:
        client_host = getattr(request.client, "host", None) if request.client else None
        await _audit_append(
            pool,
            actor="dashboard",
            action="connector.disconnect",
            target=target,
            note=(
                f"Connector '{target}' disconnect submitted for approval (action_id={action_id})"
            ),
            ip=client_host,
        )
    except Exception:
        logger.warning(
            "disconnect: failed to append audit_log entry for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )

    logger.info(
        "Disconnect submitted for connector %s/%s (action_id=%s)",
        connector_type,
        endpoint_identity,
        action_id,
    )

    return ApiResponse[dict](
        data={
            "status": "pending_approval",
            "action_id": str(action_id),
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "message": (
                f"Connector '{target}' disconnect queued for approval. "
                "The connector will be soft-deleted when the action is approved."
            ),
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/rotate-token
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/rotate-token",
    response_model=ApiResponse[dict],
    status_code=202,
)
async def rotate_connector_token(
    connector_type: str,
    endpoint_identity: str,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Rotate a connector's credential — Approvals-gated; ``is_sensitive=True`` masking (§4.5).

    Submits a pending approval action for the rotate-token operation.  The
    new credential MUST NOT appear in the response, request log, or audit log.
    ``is_sensitive=True`` masking is applied throughout.

    The response body contains ONLY ``{success: true, rotated_at: <iso8601>}``
    upon successful submission — no credential value appears anywhere.

    Returns HTTP 202 on success.
    Returns HTTP 404 if the connector is not found.
    Returns HTTP 503 if the connector registry or approvals subsystem is unavailable.

    Credential masking guarantee:
    - Request body fields carrying the new credential are marked ``is_sensitive=True``
    - Audit log entry text contains NO credential value
    - Response body contains ONLY ``{success, rotated_at}``
    """
    pool = _pool(db)

    # Verify connector exists before creating a pending action
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "rotate-token: failed to fetch connector %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    rotated_at = now.isoformat()
    target = f"{connector_type}/{endpoint_identity}"

    # Sensitive tool_args: credential fields are intentionally OMITTED from the
    # pending_action record and all log lines — is_sensitive=True masking contract.
    # Only non-sensitive metadata goes into tool_args.
    tool_args = {
        "connector_type": connector_type,
        "endpoint_identity": endpoint_identity,
        "is_sensitive": True,
        # NOTE: no token/credential field here — credential is never logged
    }

    # 72-hour expiry for lifecycle approval actions
    expires_at = now + timedelta(hours=72)

    try:
        await pool.execute(
            "INSERT INTO pending_actions"
            " (id, tool_name, tool_args, agent_summary, status, requested_at, expires_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            action_id,
            "connector_rotate_token",
            json.dumps(tool_args),
            f"Rotate credential for connector '{target}' [SENSITIVE — credential redacted]",
            "pending",
            now,
            expires_at,
        )
    except Exception:
        logger.warning(
            "rotate-token: failed to insert pending_action for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Approvals subsystem is not available")

    # Audit entry — credential value MUST NOT appear in any field
    try:
        client_host = getattr(request.client, "host", None) if request.client else None
        await _audit_append(
            pool,
            actor="dashboard",
            action="connector.rotate_token",
            target=target,
            note=(
                f"Credential rotation submitted for connector '{target}' "
                f"(action_id={action_id}) [SENSITIVE — credential omitted from log]"
            ),
            ip=client_host,
        )
    except Exception:
        logger.warning(
            "rotate-token: failed to append audit_log for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )

    logger.info(
        "rotate-token: submitted for connector %s/%s (action_id=%s) [credential redacted]",
        connector_type,
        endpoint_identity,
        action_id,
    )

    # Response MUST contain ONLY {success, rotated_at} — no credential, no action_id in data
    return ApiResponse[dict](
        data={
            "success": True,
            "rotated_at": rotated_at,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/reauth
# ---------------------------------------------------------------------------


@router.post(
    "/{connector_type}/{endpoint_identity}/reauth",
    status_code=503,
)
async def reauth_connector(
    connector_type: str,
    endpoint_identity: str,
) -> dict:
    """Reauth connector — BLOCKED until ``connector-oauth-scope-surface`` spec exists (§4.6).

    This endpoint is permanently blocked at the handler level with HTTP 503
    until the ``connector-oauth-scope-surface`` spec is ratified and implemented.

    The response body identifies the blocking spec dependency by name.
    No ``Retry-After`` header is set — recovery requires spec creation, not time.

    No Approvals-module call is made; the request is rejected before any
    approval entry is created.
    """
    raise HTTPException(
        status_code=503,
        detail={
            "blocked_by_spec": _OAUTH_SCOPE_SURFACE_SPEC,
            "message": (
                f"The reauth action is blocked until the '{_OAUTH_SCOPE_SURFACE_SPEC}' "
                "spec is ratified. This endpoint will return HTTP 503 until that spec "
                "exists in openspec/specs/. No Retry-After applies — recovery requires "
                "spec creation, not time."
            ),
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
        },
    )


# ---------------------------------------------------------------------------
# Static connector profile catalog
#
# These are the connector types the framework can deploy, independent of
# whether any instance is currently registered in connector_registry.
# The response is safe to cache on the client for at least 60 seconds.
#
# Fields: connector_type, channel, provider, display_name, supports_backfill
# ---------------------------------------------------------------------------

_CONNECTOR_CATALOG: list[dict[str, Any]] = [
    {
        "connector_type": "gmail",
        "channel": "email",
        "provider": "google",
        "display_name": "Gmail",
        "supports_backfill": True,
    },
    {
        "connector_type": "telegram_bot",
        "channel": "telegram",
        "provider": "telegram",
        "display_name": "Telegram Bot",
        "supports_backfill": False,
    },
    {
        "connector_type": "telegram_user_client",
        "channel": "telegram",
        "provider": "telegram",
        "display_name": "Telegram User Client",
        "supports_backfill": True,
    },
    {
        "connector_type": "home_assistant",
        "channel": "home-assistant",
        "provider": "home_assistant",
        "display_name": "Home Assistant",
        "supports_backfill": False,
    },
    {
        "connector_type": "discord_user",
        "channel": "discord",
        "provider": "discord",
        "display_name": "Discord User Client",
        "supports_backfill": True,
    },
    {
        "connector_type": "spotify",
        "channel": "spotify",
        "provider": "spotify",
        "display_name": "Spotify",
        "supports_backfill": False,
    },
    {
        "connector_type": "owntracks",
        "channel": "owntracks",
        "provider": "owntracks",
        "display_name": "OwnTracks",
        "supports_backfill": False,
    },
    {
        "connector_type": "whatsapp_user_client",
        "channel": "whatsapp",
        "provider": "whatsapp",
        "display_name": "WhatsApp User Client",
        "supports_backfill": False,
    },
    {
        "connector_type": "steam",
        "channel": "steam",
        "provider": "steam",
        "display_name": "Steam",
        "supports_backfill": False,
    },
    {
        "connector_type": "google_calendar",
        "channel": "google_calendar",
        "provider": "google",
        "display_name": "Google Calendar",
        "supports_backfill": True,
    },
    {
        "connector_type": "google_drive",
        "channel": "google_drive",
        "provider": "google",
        "display_name": "Google Drive",
        "supports_backfill": True,
    },
    {
        "connector_type": "google_health",
        "channel": "google_health",
        "provider": "google",
        "display_name": "Google Health",
        "supports_backfill": True,
    },
]


class ConnectorProfile(BaseModel):
    """A single connector profile entry from the discovery catalog."""

    connector_type: str
    channel: str
    provider: str
    display_name: str
    supports_backfill: bool


class ConnectorAvailableResponse(BaseModel):
    """Response body for GET /api/ingestion/connectors/available."""

    data: list[ConnectorProfile]


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/available
# ---------------------------------------------------------------------------


@router.get("/available", response_model=ConnectorAvailableResponse)
async def list_available_connectors() -> ConnectorAvailableResponse:
    """Return the list of connector profiles the framework can deploy.

    The response is independent of whether any instance is currently
    registered in connector_registry.  Suitable for client-side caching
    for at least 60 seconds.

    Used by the dashboard "add connector" affordance and the
    ConnectorsListPage dormant/available section (§3.5).
    """
    profiles = [ConnectorProfile(**p) for p in _CONNECTOR_CATALOG]
    return ConnectorAvailableResponse(data=profiles)


# ---------------------------------------------------------------------------
# Connector-scoped event and rule endpoints [bu-5ywn2]
# ---------------------------------------------------------------------------

_MAX_CONNECTOR_EVENTS = 100
_MAX_CONNECTOR_INCIDENTS = 50
_DEFAULT_CONNECTOR_EVENTS = 20
_DEFAULT_CONNECTOR_INCIDENTS = 10

#: Status values that classify an ingestion event as an incident
_INCIDENT_STATUSES: frozenset[str] = frozenset({"failed", "error", "replay_failed"})


class ConnectorEventSummary(BaseModel):
    """A single event row returned from the connector-scoped events endpoint."""

    id: str
    received_at: str | None
    source_channel: str | None
    source_sender_identity: str | None
    status: str
    filter_reason: str | None
    error_detail: str | None


class ConnectorEventsResponse(BaseModel):
    """Response envelope for GET …/{type}/{identity}/events."""

    events: list[ConnectorEventSummary]
    connector_type: str
    endpoint_identity: str
    total_returned: int


class ConnectorIncidentSummary(BaseModel):
    """A single incident row returned from the connector-scoped incidents endpoint."""

    id: str
    received_at: str | None
    source_channel: str | None
    status: str
    error_detail: str | None
    filter_reason: str | None


class ConnectorIncidentsResponse(BaseModel):
    """Response envelope for GET …/{type}/{identity}/incidents."""

    incidents: list[ConnectorIncidentSummary]
    connector_type: str
    endpoint_identity: str
    total_returned: int


class ConnectorRoutingRule(BaseModel):
    """A single ingestion rule referencing this connector."""

    id: str
    scope: str
    rule_type: str
    condition: dict[str, Any]
    action: str
    priority: int
    enabled: bool
    name: str | None
    description: str | None
    created_by: str
    created_at: str
    updated_at: str


class ConnectorRoutingRulesResponse(BaseModel):
    """Response envelope for GET …/{type}/{identity}/routing-rules."""

    rules: list[ConnectorRoutingRule]
    connector_type: str
    endpoint_identity: str
    total_returned: int
    filter_note: str | None = None


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/events
# ---------------------------------------------------------------------------


@router.get(
    "/{connector_type}/{endpoint_identity}/events",
    response_model=ConnectorEventsResponse,
)
async def list_connector_events(
    connector_type: str,
    endpoint_identity: str,
    limit: int = Query(
        _DEFAULT_CONNECTOR_EVENTS,
        ge=1,
        le=_MAX_CONNECTOR_EVENTS,
        description="Max events to return (default 20, max 100)",
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConnectorEventsResponse:
    """Return the most recent events for a specific connector.

    Queries the unified ingestion timeline (public.ingestion_events UNION ALL
    connectors.filtered_events) scoped to this connector's (connector_type,
    endpoint_identity) pair.  Results are ordered newest first.

    public.ingestion_events is matched via source_channel (connector_type) AND
    source_endpoint_identity (endpoint_identity).
    connectors.filtered_events is matched via connector_type AND endpoint_identity.

    Returns HTTP 404 if the connector is not in the registry.
    Returns HTTP 503 if the connector registry is unavailable.
    """
    pool = _pool(db)

    # Verify connector exists in registry before querying events
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "connector-events: registry lookup failed for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    # Query unified event timeline scoped to this connector.
    # Two sources:
    #   1. public.ingestion_events — matched via source_endpoint_identity
    #   2. connectors.filtered_events — matched via connector_type + endpoint_identity
    try:
        rows = await pool.fetch(
            """
            SELECT
                id::text AS id,
                received_at,
                source_channel,
                source_sender_identity,
                status,
                NULL::text AS filter_reason,
                error_detail
            FROM public.ingestion_events
            WHERE source_endpoint_identity = $2
              AND source_channel = $1
            UNION ALL
            SELECT
                id::text AS id,
                received_at,
                source_channel,
                sender_identity AS source_sender_identity,
                status,
                filter_reason,
                error_detail
            FROM connectors.filtered_events
            WHERE connector_type = $1
              AND endpoint_identity = $2
            ORDER BY received_at DESC NULLS LAST
            LIMIT $3
            """,
            connector_type,
            endpoint_identity,
            limit,
        )
    except Exception:
        logger.warning(
            "connector-events: failed to query events for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Event query failed")

    events = [
        ConnectorEventSummary(
            id=str(r["id"]),
            received_at=r["received_at"].isoformat() if r["received_at"] else None,
            source_channel=r["source_channel"],
            source_sender_identity=r["source_sender_identity"],
            status=r["status"],
            filter_reason=r["filter_reason"],
            error_detail=r["error_detail"],
        )
        for r in rows
    ]

    return ConnectorEventsResponse(
        events=events,
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
        total_returned=len(events),
    )


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/incidents
# ---------------------------------------------------------------------------


@router.get(
    "/{connector_type}/{endpoint_identity}/incidents",
    response_model=ConnectorIncidentsResponse,
)
async def list_connector_incidents(
    connector_type: str,
    endpoint_identity: str,
    limit: int = Query(
        _DEFAULT_CONNECTOR_INCIDENTS,
        ge=1,
        le=_MAX_CONNECTOR_INCIDENTS,
        description="Max incidents to return (default 10, max 50)",
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConnectorIncidentsResponse:
    """Return recent incident events (failures and errors) for a specific connector.

    An incident is any event with status in ('failed', 'error', 'replay_failed').
    This represents degraded or failed processing — distinct from the full event
    stream which includes successfully ingested events.

    Queries both public.ingestion_events and connectors.filtered_events, each
    filtered by the incident status set and scoped to this connector.  Results
    are ordered newest first.

    Returns HTTP 404 if the connector is not in the registry.
    Returns HTTP 503 if the connector registry is unavailable.
    """
    pool = _pool(db)

    # Verify connector exists in registry before querying incidents
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "connector-incidents: registry lookup failed for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    # Build ordered incident status tuple for parameterised ANY() check
    incident_statuses = list(_INCIDENT_STATUSES)

    try:
        rows = await pool.fetch(
            """
            SELECT
                id::text AS id,
                received_at,
                source_channel,
                status,
                error_detail,
                NULL::text AS filter_reason
            FROM public.ingestion_events
            WHERE source_endpoint_identity = $2
              AND source_channel = $1
              AND status = ANY($3::text[])
            UNION ALL
            SELECT
                id::text AS id,
                received_at,
                source_channel,
                status,
                error_detail,
                filter_reason
            FROM connectors.filtered_events
            WHERE connector_type = $1
              AND endpoint_identity = $2
              AND status = ANY($3::text[])
            ORDER BY received_at DESC NULLS LAST
            LIMIT $4
            """,
            connector_type,
            endpoint_identity,
            incident_statuses,
            limit,
        )
    except Exception:
        logger.warning(
            "connector-incidents: failed to query incidents for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Incident query failed")

    incidents = [
        ConnectorIncidentSummary(
            id=str(r["id"]),
            received_at=r["received_at"].isoformat() if r["received_at"] else None,
            source_channel=r["source_channel"],
            status=r["status"],
            error_detail=r["error_detail"],
            filter_reason=r["filter_reason"],
        )
        for r in rows
    ]

    return ConnectorIncidentsResponse(
        incidents=incidents,
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
        total_returned=len(incidents),
    )


# ---------------------------------------------------------------------------
# GET /api/ingestion/connectors/{type}/{identity}/routing-rules
# ---------------------------------------------------------------------------


@router.get(
    "/{connector_type}/{endpoint_identity}/routing-rules",
    response_model=ConnectorRoutingRulesResponse,
)
async def list_connector_routing_rules(
    connector_type: str,
    endpoint_identity: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConnectorRoutingRulesResponse:
    """Return ingestion rules referencing this connector.

    Queries the ``ingestion_rules`` table for rules whose ``scope`` matches
    the structured connector scope: ``'connector:<connector_type>:<endpoint_identity>'``.

    This is a precise structured match — no text search is needed because the
    scope column encodes connector identity explicitly in the format defined
    by design.md D2.

    Results are ordered by priority ASC, created_at ASC, id ASC (same as
    the global rule list endpoint).

    Returns HTTP 404 if the connector is not in the registry.
    Returns HTTP 503 if the connector registry or rules table is unavailable.
    """
    pool = _pool(db)

    # Verify connector exists in registry
    try:
        existing = await pool.fetchrow(
            "SELECT connector_type, endpoint_identity FROM connector_registry"
            " WHERE connector_type = $1 AND endpoint_identity = $2 AND deleted_at IS NULL",
            connector_type,
            endpoint_identity,
        )
    except Exception:
        logger.warning(
            "connector-routing-rules: registry lookup failed for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Connector registry is not available")

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_type}/{endpoint_identity}' not found",
        )

    # Structured scope match: design.md D2 format is 'connector:<type>:<identity>'
    connector_scope = f"connector:{connector_type}:{endpoint_identity}"

    try:
        rows = await pool.fetch(
            """
            SELECT
                id::text AS id,
                scope,
                rule_type,
                condition,
                action,
                priority,
                enabled,
                name,
                description,
                created_by,
                created_at,
                updated_at
            FROM ingestion_rules
            WHERE scope = $1
              AND deleted_at IS NULL
            ORDER BY priority ASC, created_at ASC, id ASC
            """,
            connector_scope,
        )
    except Exception:
        logger.warning(
            "connector-routing-rules: failed to query rules for %s/%s",
            connector_type,
            endpoint_identity,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Routing rules query failed")

    rules = []
    for r in rows:
        condition = r["condition"]
        if isinstance(condition, str):
            condition = json.loads(condition)
        rules.append(
            ConnectorRoutingRule(
                id=str(r["id"]),
                scope=r["scope"],
                rule_type=r["rule_type"],
                condition=condition,
                action=r["action"],
                priority=r["priority"],
                enabled=r["enabled"],
                name=r["name"],
                description=r["description"],
                created_by=r["created_by"],
                created_at=r["created_at"].isoformat(),
                updated_at=r["updated_at"].isoformat(),
            )
        )

    return ConnectorRoutingRulesResponse(
        rules=rules,
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
        total_returned=len(rules),
    )
