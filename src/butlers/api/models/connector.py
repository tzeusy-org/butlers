"""Pydantic models for the connectors dashboard API.

Maps to connector registry and statistics tables, providing response models
for connector list, detail, stats time-series, and fanout distribution views.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


def derive_liveness(last_heartbeat_at: datetime | None) -> str:
    """Derive liveness status from last heartbeat timestamp.

    Liveness thresholds (from docs/connectors/heartbeat.md):
    - online: heartbeat within last 5 minutes
    - stale: heartbeat between 5-15 minutes ago
    - offline: no heartbeat for 15+ minutes or never seen

    Args:
        last_heartbeat_at: Timestamp of the last received heartbeat, or None if never seen

    Returns:
        One of: "online", "stale", "offline"
    """
    if last_heartbeat_at is None:
        return "offline"

    import datetime as dt

    now = dt.datetime.now(dt.UTC)
    age = (now - last_heartbeat_at).total_seconds()

    if age <= 300:  # 5 minutes
        return "online"
    elif age <= 900:  # 15 minutes
        return "stale"
    else:
        return "offline"


class ConnectorDaySummary(BaseModel):
    """Today's aggregated metrics for a connector."""

    messages_ingested: int = 0
    messages_failed: int = 0
    uptime_pct: float | None = None


class ConnectorSummary(BaseModel):
    """Lightweight connector representation for list views.

    Combines static registry data (connector_type, endpoint_identity) with
    live heartbeat state (liveness, state, error_message, version, uptime).
    When a connector has not sent a heartbeat for 15+ minutes, liveness is
    set to "offline".
    """

    connector_type: str
    endpoint_identity: str
    liveness: str  # online, stale, offline (derived from last_heartbeat_at)
    state: str  # healthy, degraded, error (from heartbeat)
    error_message: str | None = None
    version: str | None = None
    uptime_s: int | None = None
    last_heartbeat_at: datetime | None = None
    first_seen_at: datetime
    today: ConnectorDaySummary | None = None


class ConnectorCheckpoint(BaseModel):
    """Connector checkpoint state from the last heartbeat."""

    cursor: str | None = None
    updated_at: datetime | None = None


class ConnectorCounters(BaseModel):
    """Lifetime counters from the connector's last heartbeat."""

    messages_ingested: int = 0
    messages_failed: int = 0
    source_api_calls: int = 0
    checkpoint_saves: int = 0
    dedupe_accepted: int = 0


class ConnectorDetail(ConnectorSummary):
    """Full connector detail with instance ID, registration source, checkpoint, and counters.

    Extends ConnectorSummary with additional fields from the connector registry
    and the most recent heartbeat.
    """

    instance_id: UUID | None = None
    registered_via: str = "self"
    checkpoint: ConnectorCheckpoint | None = None
    counters: ConnectorCounters | None = None


class ConnectorStatsBucket(BaseModel):
    """A single time bucket in a connector stats time-series.

    For 24h/7d periods, buckets are hourly (from connector_stats_hourly).
    For 30d period, buckets are daily (from connector_stats_daily).
    """

    bucket: datetime
    messages_ingested: int = 0
    messages_failed: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    error_count: int = 0


class ConnectorStatsSummary(BaseModel):
    """Aggregated summary statistics for a connector over a time period."""

    messages_ingested: int = 0
    messages_failed: int = 0
    error_rate_pct: float = 0.0
    uptime_pct: float | None = None
    avg_messages_per_hour: float = 0.0


class ConnectorStats(BaseModel):
    """Volume and health statistics for a connector over a time period.

    Includes both a summary aggregation and a time-series breakdown into
    hourly or daily buckets depending on the period.
    """

    connector_type: str
    endpoint_identity: str
    period: str  # 24h, 7d, 30d
    summary: ConnectorStatsSummary
    timeseries: list[ConnectorStatsBucket] = Field(default_factory=list)


class ConnectorFanoutEntry(BaseModel):
    """Message routing distribution from one connector to downstream butlers.

    Maps connector -> butler fanout for a given time period. The targets dict
    contains butler names as keys and message counts as values.
    """

    connector_type: str
    endpoint_identity: str
    targets: dict[str, int] = Field(default_factory=dict)  # butler_name -> message_count
