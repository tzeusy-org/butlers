"""Connector heartbeat ingestion and liveness tracking.

This module implements the `connector.heartbeat` MCP tool that accepts
connector.heartbeat.v1 envelopes from connectors and persists them to
the connector_registry and connector_heartbeat_log tables.

Key behaviors:
- Self-registers unknown (connector_type, endpoint_identity) pairs on first heartbeat
- Upserts connector_registry with latest state, counters, and checkpoint
- Appends to connector_heartbeat_log for historical tracking and rollups
- Computes counter deltas from previous snapshot (for future statistics aggregation)
- Logs instance_id changes (connector restart detection)
- Returns {status: 'accepted', server_time: RFC3339} to the connector

See docs/connectors/heartbeat.md for the full protocol specification.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

logger = logging.getLogger(__name__)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ConnectorState = Literal["healthy", "degraded", "error"]


class ConnectorIdentityV1(BaseModel):
    """Connector identity section of heartbeat.v1 envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connector_type: NonEmptyStr = Field(
        description="Canonical connector type (e.g., telegram_bot, gmail, imap)"
    )
    endpoint_identity: NonEmptyStr = Field(
        description="The receiving identity this connector serves"
    )
    instance_id: UUID = Field(
        description="Stable UUID for this process instance, generated at startup"
    )
    version: str | None = Field(
        default=None,
        description="Optional connector software version (semver or git sha)",
    )


class ConnectorStatusV1(BaseModel):
    """Connector status section of heartbeat.v1 envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ConnectorState = Field(
        description="Current operational state: healthy, degraded, or error"
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error context when state is degraded or error",
    )
    uptime_s: int = Field(
        ge=0,
        description="Seconds since this connector instance started",
    )

    @field_validator("error_message")
    @classmethod
    def validate_error_message(cls, v: str | None, info) -> str | None:
        """Ensure error_message is present when state is degraded or error."""
        state = info.data.get("state")
        if state in ("degraded", "error") and not v:
            logger.warning(
                "Heartbeat has state=%s but no error_message; allowing but flagging", state
            )
        return v


class ConnectorCountersV1(BaseModel):
    """Connector counters section of heartbeat.v1 envelope.

    All counters are monotonically increasing since process start (not since last heartbeat).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages_ingested: int = Field(
        ge=0,
        description="Total messages successfully submitted to Switchboard ingest API",
    )
    messages_failed: int = Field(
        ge=0,
        description="Total messages that failed ingest submission (after retries exhausted)",
    )
    source_api_calls: int = Field(
        ge=0,
        description="Total calls made to the source provider API",
    )
    checkpoint_saves: int = Field(
        ge=0,
        description="Total checkpoint persistence operations",
    )
    dedupe_accepted: int = Field(
        ge=0,
        default=0,
        description="Total messages accepted by Switchboard as duplicates (not errors)",
    )


class ConnectorCheckpointV1(BaseModel):
    """Connector checkpoint section of heartbeat.v1 envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: str | None = Field(
        default=None,
        description="Opaque provider-specific checkpoint value (e.g., Telegram update_id)",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Timestamp of last checkpoint advance",
    )

    @field_validator("updated_at", mode="before")
    @classmethod
    def parse_timestamp(cls, v: Any) -> datetime | None:
        """Parse RFC3339 timestamp string to datetime."""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        raise ValueError(f"Invalid timestamp format: {v}")


class ConnectorHeartbeatV1(BaseModel):
    """connector.heartbeat.v1 envelope schema.

    Connectors submit this payload to the Switchboard via the `connector.heartbeat`
    MCP tool to report liveness and operational statistics.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["connector.heartbeat.v1"] = Field(
        description="Envelope schema version (must be connector.heartbeat.v1)"
    )
    connector: ConnectorIdentityV1 = Field(
        description="Connector identity (type, endpoint, instance)"
    )
    status: ConnectorStatusV1 = Field(description="Current operational state")
    counters: ConnectorCountersV1 = Field(description="Monotonic operational counters")
    checkpoint: ConnectorCheckpointV1 | None = Field(
        default=None,
        description="Optional checkpoint state from last provider poll",
    )
    sent_at: datetime = Field(
        description="Timestamp when this heartbeat was generated by the connector"
    )

    @field_validator("sent_at", mode="before")
    @classmethod
    def parse_sent_at(cls, v: Any) -> datetime:
        """Parse RFC3339 timestamp string to datetime."""
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        raise ValueError(f"Invalid timestamp format: {v}")


class HeartbeatAcceptedResponse(BaseModel):
    """Response payload for accepted heartbeat submissions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = "accepted"
    server_time: str = Field(description="Server timestamp in RFC3339 format")


def parse_connector_heartbeat(payload: Mapping[str, Any]) -> ConnectorHeartbeatV1:
    """Parse and validate a connector.heartbeat.v1 envelope.

    Args:
        payload: Raw heartbeat envelope payload from MCP tool call.

    Returns:
        Validated ConnectorHeartbeatV1 model.

    Raises:
        ValueError: If the payload fails validation.
    """
    try:
        return ConnectorHeartbeatV1.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"Invalid connector.heartbeat.v1 envelope: {exc}") from exc


async def _get_previous_snapshot(
    pool: asyncpg.Pool,
    connector_type: str,
    endpoint_identity: str,
) -> asyncpg.Record | None:
    """Fetch the previous counter snapshot from connector_registry.

    Returns None if this is the first heartbeat (self-registration case).
    """
    return await pool.fetchrow(
        """
        SELECT
            instance_id,
            counter_messages_ingested,
            counter_messages_failed,
            counter_source_api_calls,
            counter_checkpoint_saves,
            counter_dedupe_accepted
        FROM connector_registry
        WHERE connector_type = $1 AND endpoint_identity = $2
        """,
        connector_type,
        endpoint_identity,
    )


def _compute_counter_deltas(
    current: ConnectorCountersV1,
    previous: asyncpg.Record | None,
    instance_id: UUID,
) -> dict[str, int]:
    """Compute counter deltas from previous snapshot.

    If this is a new instance_id (restart), deltas are the current values
    (restart resets counters to current snapshot).

    If instance_id matches, deltas are the difference from the previous snapshot.

    Returns a dict of delta values for each counter.
    """
    if previous is None:
        # First heartbeat ever — deltas are the current values
        return {
            "messages_ingested_delta": current.messages_ingested,
            "messages_failed_delta": current.messages_failed,
            "source_api_calls_delta": current.source_api_calls,
            "checkpoint_saves_delta": current.checkpoint_saves,
            "dedupe_accepted_delta": current.dedupe_accepted,
        }

    prev_instance_id = previous["instance_id"]
    if prev_instance_id != instance_id:
        # Instance ID changed — connector restarted, deltas are current values
        logger.info(
            "Instance ID changed from %s to %s — connector restarted",
            prev_instance_id,
            instance_id,
        )
        return {
            "messages_ingested_delta": current.messages_ingested,
            "messages_failed_delta": current.messages_failed,
            "source_api_calls_delta": current.source_api_calls,
            "checkpoint_saves_delta": current.checkpoint_saves,
            "dedupe_accepted_delta": current.dedupe_accepted,
        }

    # Same instance — compute deltas
    return {
        "messages_ingested_delta": (
            current.messages_ingested - previous["counter_messages_ingested"]
        ),
        "messages_failed_delta": (current.messages_failed - previous["counter_messages_failed"]),
        "source_api_calls_delta": (current.source_api_calls - previous["counter_source_api_calls"]),
        "checkpoint_saves_delta": (current.checkpoint_saves - previous["counter_checkpoint_saves"]),
        "dedupe_accepted_delta": (current.dedupe_accepted - previous["counter_dedupe_accepted"]),
    }


async def heartbeat(
    pool: asyncpg.Pool,
    payload: Mapping[str, Any],
) -> HeartbeatAcceptedResponse:
    """Accept and persist a connector.heartbeat.v1 envelope.

    This is the canonical heartbeat ingestion boundary for connector liveness tracking.

    On first heartbeat from an unknown (connector_type, endpoint_identity) pair,
    self-registers the connector in the registry.

    On subsequent heartbeats:
    - Upserts connector_registry with latest state, counters, and checkpoint
    - Appends to connector_heartbeat_log for historical tracking
    - Computes counter deltas from previous snapshot
    - Logs instance_id changes (connector restart detection)

    Args:
        pool: Database connection pool for Switchboard butler.
        payload: Raw heartbeat envelope payload (must validate as connector.heartbeat.v1).

    Returns:
        HeartbeatAcceptedResponse with server_time for clock drift detection.

    Raises:
        ValueError: If the payload fails connector.heartbeat.v1 validation.
        RuntimeError: If database persistence fails unexpectedly.
    """
    # 1. Parse and validate envelope
    try:
        envelope = parse_connector_heartbeat(payload)
    except Exception as exc:
        logger.warning("Heartbeat envelope validation failed: %s", exc)
        raise ValueError(f"Invalid connector.heartbeat.v1 envelope: {exc}") from exc

    connector = envelope.connector
    status = envelope.status
    counters = envelope.counters
    checkpoint = envelope.checkpoint
    sent_at = envelope.sent_at
    received_at = datetime.now(UTC)

    connector_type = connector.connector_type
    endpoint_identity = connector.endpoint_identity
    instance_id = connector.instance_id

    # 2. Fetch previous snapshot for delta computation
    previous = await _get_previous_snapshot(pool, connector_type, endpoint_identity)

    # 3. Compute counter deltas
    deltas = _compute_counter_deltas(counters, previous, instance_id)

    # 4. Upsert connector_registry
    if previous is None:
        # Self-registration: first heartbeat
        logger.info(
            "Self-registering connector: connector_type=%s, endpoint_identity=%s, instance_id=%s",
            connector_type,
            endpoint_identity,
            instance_id,
        )

    try:
        await pool.execute(
            """
            INSERT INTO connector_registry (
                connector_type,
                endpoint_identity,
                instance_id,
                version,
                state,
                error_message,
                uptime_s,
                last_heartbeat_at,
                first_seen_at,
                registered_via,
                counter_messages_ingested,
                counter_messages_failed,
                counter_source_api_calls,
                counter_checkpoint_saves,
                counter_dedupe_accepted,
                checkpoint_cursor,
                checkpoint_updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $8, 'self',
                $9, $10, $11, $12, $13, $14, $15
            )
            ON CONFLICT (connector_type, endpoint_identity) DO UPDATE SET
                instance_id = EXCLUDED.instance_id,
                version = EXCLUDED.version,
                state = EXCLUDED.state,
                error_message = EXCLUDED.error_message,
                uptime_s = EXCLUDED.uptime_s,
                last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                counter_messages_ingested = EXCLUDED.counter_messages_ingested,
                counter_messages_failed = EXCLUDED.counter_messages_failed,
                counter_source_api_calls = EXCLUDED.counter_source_api_calls,
                counter_checkpoint_saves = EXCLUDED.counter_checkpoint_saves,
                counter_dedupe_accepted = EXCLUDED.counter_dedupe_accepted,
                checkpoint_cursor = EXCLUDED.checkpoint_cursor,
                checkpoint_updated_at = EXCLUDED.checkpoint_updated_at
            """,
            connector_type,
            endpoint_identity,
            instance_id,
            connector.version,
            status.state,
            status.error_message,
            status.uptime_s,
            received_at,
            counters.messages_ingested,
            counters.messages_failed,
            counters.source_api_calls,
            counters.checkpoint_saves,
            counters.dedupe_accepted,
            checkpoint.cursor if checkpoint else None,
            checkpoint.updated_at if checkpoint else None,
        )
    except Exception as exc:
        logger.error(
            "Failed to upsert connector_registry for %s/%s: %s",
            connector_type,
            endpoint_identity,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to persist connector heartbeat: {exc}") from exc

    # 5. Ensure partition exists for received_at
    try:
        await pool.execute(
            "SELECT switchboard_connector_heartbeat_log_ensure_partition($1)",
            received_at,
        )
    except Exception as exc:
        logger.error("Failed to ensure partition for %s: %s", received_at, exc, exc_info=True)
        # Non-fatal: log insertion might still succeed if partition exists

    # 6. Append to connector_heartbeat_log
    try:
        await pool.execute(
            """
            INSERT INTO connector_heartbeat_log (
                connector_type,
                endpoint_identity,
                instance_id,
                state,
                error_message,
                uptime_s,
                counter_messages_ingested,
                counter_messages_failed,
                counter_source_api_calls,
                counter_checkpoint_saves,
                counter_dedupe_accepted,
                received_at,
                sent_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
            )
            """,
            connector_type,
            endpoint_identity,
            instance_id,
            status.state,
            status.error_message,
            status.uptime_s,
            counters.messages_ingested,
            counters.messages_failed,
            counters.source_api_calls,
            counters.checkpoint_saves,
            counters.dedupe_accepted,
            received_at,
            sent_at,
        )
    except Exception as exc:
        logger.error(
            "Failed to append to connector_heartbeat_log for %s/%s: %s",
            connector_type,
            endpoint_identity,
            exc,
            exc_info=True,
        )
        # Registry update succeeded, so heartbeat is accepted.
        # Log append failure is non-fatal for basic liveness tracking.

    logger.info(
        "Accepted heartbeat: connector_type=%s, endpoint_identity=%s, instance_id=%s, "
        "state=%s, uptime=%ds, deltas=%s",
        connector_type,
        endpoint_identity,
        instance_id,
        status.state,
        status.uptime_s,
        deltas,
    )

    return HeartbeatAcceptedResponse(
        status="accepted",
        server_time=received_at.isoformat(),
    )
