"""Pydantic models for the switchboard API.

Provides models for routing log, registry, connector ingestion, and
ingestion overview entries (switchboard butler).
"""

from __future__ import annotations

from pydantic import BaseModel


class RoutingEntry(BaseModel):
    """A single entry in the switchboard routing log."""

    id: str
    source_butler: str
    target_butler: str
    tool_name: str
    success: bool
    duration_ms: int | None = None
    error: str | None = None
    created_at: str


class RegistryEntry(BaseModel):
    """A butler entry in the switchboard registry."""

    name: str
    endpoint_url: str
    description: str | None = None
    modules: list = []
    capabilities: list = []
    last_seen_at: str | None = None
    eligibility_state: str = "active"
    liveness_ttl_seconds: int = 300
    quarantined_at: str | None = None
    quarantine_reason: str | None = None
    route_contract_min: int = 1
    route_contract_max: int = 1
    eligibility_updated_at: str | None = None
    registered_at: str


class HeartbeatRequest(BaseModel):
    """Request body for the POST /api/heartbeat endpoint."""

    butler_name: str


class HeartbeatResponse(BaseModel):
    """Response body for the POST /api/heartbeat endpoint."""

    status: str
    eligibility_state: str


# ---------------------------------------------------------------------------
# Connector ingestion models
# ---------------------------------------------------------------------------


class ConnectorEntry(BaseModel):
    """A connector entry from the connector_registry table.

    Fields align with what is needed for Overview/Connectors tab cards and
    health badge rows in the ingestion dashboard.
    """

    connector_type: str
    endpoint_identity: str
    instance_id: str | None = None
    version: str | None = None
    state: str = "unknown"
    error_message: str | None = None
    uptime_s: int | None = None
    last_heartbeat_at: str | None = None
    first_seen_at: str
    registered_via: str = "self"
    # Cumulative counters
    counter_messages_ingested: int = 0
    counter_messages_failed: int = 0
    counter_source_api_calls: int = 0
    counter_checkpoint_saves: int = 0
    counter_dedupe_accepted: int = 0
    # Checkpoint info
    checkpoint_cursor: str | None = None
    checkpoint_updated_at: str | None = None


class ConnectorSummary(BaseModel):
    """Aggregate summary across all connectors.

    Drives the summary row at the top of the Connectors tab.
    """

    total_connectors: int = 0
    online_count: int = 0
    stale_count: int = 0
    offline_count: int = 0
    unknown_count: int = 0
    total_messages_ingested: int = 0
    total_messages_failed: int = 0
    error_rate_pct: float = 0.0


class ConnectorStatsHourly(BaseModel):
    """Hourly rollup stats for a single connector.

    Drives volume trend charts with 24h/7d/30d period support.
    """

    connector_type: str
    endpoint_identity: str
    hour: str
    messages_ingested: int = 0
    messages_failed: int = 0
    source_api_calls: int = 0
    dedupe_accepted: int = 0
    heartbeat_count: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    error_count: int = 0


class ConnectorStatsDaily(BaseModel):
    """Daily rollup stats for a single connector.

    Drives volume trend charts for the 7d/30d period options.
    """

    connector_type: str
    endpoint_identity: str
    day: str
    messages_ingested: int = 0
    messages_failed: int = 0
    source_api_calls: int = 0
    dedupe_accepted: int = 0
    heartbeat_count: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    error_count: int = 0
    uptime_pct: float | None = None


class FanoutRow(BaseModel):
    """A single row in the connector × butler fanout matrix.

    One row per (connector_type, endpoint_identity, target_butler) tuple,
    aggregated over the requested period.
    """

    connector_type: str
    endpoint_identity: str
    target_butler: str
    message_count: int = 0


class IngestionOverviewStats(BaseModel):
    """Aggregate ingestion overview statistics for the Overview tab.

    Covers the stat-row cards and derived metrics needed by the Overview tab.
    All counts are for the requested period (default 24h).
    """

    period: str = "24h"
    total_ingested: int = 0
    total_skipped: int = 0
    total_metadata_only: int = 0
    llm_calls_saved: int = 0
    active_connectors: int = 0
    # Tier breakdown counts (for donut chart)
    tier1_full_count: int = 0
    tier2_metadata_count: int = 0
    tier3_skip_count: int = 0
