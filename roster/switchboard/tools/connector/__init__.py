"""Connector management tools for Switchboard.

Provides MCP tools for connector liveness tracking, heartbeat ingestion,
and operational statistics.
"""

from roster.switchboard.tools.connector.heartbeat import (
    ConnectorHeartbeatV1,
    HeartbeatAcceptedResponse,
    heartbeat,
    parse_connector_heartbeat,
)

__all__ = [
    "ConnectorHeartbeatV1",
    "HeartbeatAcceptedResponse",
    "heartbeat",
    "parse_connector_heartbeat",
]
