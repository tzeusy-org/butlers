"""Pydantic models for the switchboard API.

Provides models for routing log and registry entries (switchboard butler).
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
