"""Pydantic models for the general butler and switchboard APIs.

Provides models for collections, entities (general butler), and
routing log / registry entries (switchboard butler).
"""

from __future__ import annotations

from pydantic import BaseModel


class Collection(BaseModel):
    """A named collection of entities in the general butler."""

    id: str
    name: str
    description: str | None = None
    entity_count: int = 0  # computed via COUNT
    created_at: str


class Entity(BaseModel):
    """An entity within a collection."""

    id: str
    collection_id: str
    collection_name: str | None = None  # joined from collections
    data: dict = {}  # JSONB
    tags: list[str] = []  # JSONB
    created_at: str
    updated_at: str


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
