"""Pydantic models for the Home butler API.

Provides models for Home Assistant entity state, areas, command log entries,
and snapshot status used by the home butler's dashboard endpoints.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EntityStateResponse(BaseModel):
    """Full state detail for a single Home Assistant entity."""

    entity_id: str
    state: str | None = None
    attributes: dict[str, Any] = {}
    last_updated: str | None = None
    captured_at: str


class EntitySummaryResponse(BaseModel):
    """Summary row for an entity in a list response."""

    entity_id: str
    state: str | None = None
    friendly_name: str | None = None
    domain: str
    last_updated: str | None = None
    captured_at: str


class AreaResponse(BaseModel):
    """An area grouping from the Home Assistant entity snapshot cache.

    The home butler does not maintain a separate areas table; areas are
    derived from the ``area_id`` attribute stored in entity snapshot
    attributes.
    """

    area_id: str
    entity_count: int


class CommandLogEntry(BaseModel):
    """A single entry in the Home Assistant command audit log."""

    id: int
    domain: str
    service: str
    target: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    context_id: str | None = None
    issued_at: str


class StatisticsResponse(BaseModel):
    """Aggregate statistics about the Home butler's entity snapshot cache."""

    total_entities: int
    domains: dict[str, int]
    oldest_captured_at: str | None = None
    newest_captured_at: str | None = None
