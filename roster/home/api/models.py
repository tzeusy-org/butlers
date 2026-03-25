"""Pydantic models for the Home butler API.

Provides models for Home Assistant entity state, areas, command log entries,
snapshot status, device inventory, energy consumption, maintenance items,
and threshold configuration used by the home butler's dashboard endpoints.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field


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


# ---------------------------------------------------------------------------
# Device Inventory
# ---------------------------------------------------------------------------


class DeviceInventoryEntry(BaseModel):
    """A single device entry in the inventory listing."""

    entity_id: str
    state: str
    friendly_name: str | None = None
    area_name: str | None = None
    domain: str
    last_updated: datetime | None = None
    health_status: Literal["healthy", "offline"]


class DevicePaginationMeta(BaseModel):
    """Page-based pagination metadata for the device inventory endpoint."""

    page: int
    page_size: int
    total_count: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pages(self) -> int:
        """Total number of pages."""
        if self.page_size <= 0:
            return 0
        return max(1, math.ceil(self.total_count / self.page_size))


class DeviceInventoryResponse(BaseModel):
    """Paginated response wrapper for device inventory."""

    data: list[DeviceInventoryEntry]
    meta: DevicePaginationMeta


# ---------------------------------------------------------------------------
# Energy Consumption
# ---------------------------------------------------------------------------


class EnergyDataPoint(BaseModel):
    """A single time-series data point for energy consumption."""

    timestamp: datetime
    total_kwh: float
    devices: dict[str, float] = Field(default_factory=dict)


class TopConsumerEntry(BaseModel):
    """A top energy-consuming device entry."""

    entity_id: str
    friendly_name: str | None = None
    total_kwh: float
    percentage: float


# ---------------------------------------------------------------------------
# Maintenance Items
# ---------------------------------------------------------------------------


class MaintenanceItemResponse(BaseModel):
    """A maintenance item with computed status."""

    id: UUID
    name: str
    category: str
    interval_days: int
    last_completed_at: datetime | None = None
    next_due_at: datetime | None = None
    status: Literal["overdue", "due", "upcoming", "ok"]
    notes: str | None = None


class MaintenanceItemCreateRequest(BaseModel):
    """Request body for creating a new maintenance item."""

    name: str
    category: str
    interval_days: int
    notes: str | None = None


# ---------------------------------------------------------------------------
# Threshold Settings
# ---------------------------------------------------------------------------


class BatteryThresholds(BaseModel):
    """Battery level thresholds for device health check."""

    critical: int = 10
    warning: int = 20
    info: int = 30


class OfflineHoursThresholds(BaseModel):
    """Offline duration thresholds for device health check."""

    critical: int = 24
    warning: int = 1


class ComfortDefaults(BaseModel):
    """Default comfort range thresholds for environment report."""

    temp_min_f: float = 68
    temp_max_f: float = 76
    humidity_min: float = 30
    humidity_max: float = 60
    co2_max_ppm: float = 1000


class ComfortDeviation(BaseModel):
    """Comfort deviation thresholds for environment report."""

    minor_temp_f: float = 2
    moderate_temp_f: float = 5
    minor_humidity: float = 10
    moderate_humidity: float = 20
    critical_temp_low_f: float = 60
    critical_temp_high_f: float = 85
    critical_co2_ppm: float = 1500
    critical_humidity_low: float = 15
    critical_humidity_high: float = 80


class EnergyThresholds(BaseModel):
    """Energy anomaly detection thresholds for energy digest."""

    anomaly_pct: float = 20
    high_severity_pct: float = 100


class ThresholdConfig(BaseModel):
    """Full threshold configuration for all home monitoring jobs."""

    battery: BatteryThresholds = Field(default_factory=BatteryThresholds)
    offline_hours: OfflineHoursThresholds = Field(default_factory=OfflineHoursThresholds)
    comfort_defaults: ComfortDefaults = Field(default_factory=ComfortDefaults)
    comfort_deviation: ComfortDeviation = Field(default_factory=ComfortDeviation)
    energy: EnergyThresholds = Field(default_factory=EnergyThresholds)


class ThresholdUpdateRequest(BaseModel):
    """Partial update request for threshold configuration.

    All fields are optional — only provided fields are merged.
    """

    battery: BatteryThresholds | None = None
    offline_hours: OfflineHoursThresholds | None = None
    comfort_defaults: ComfortDefaults | None = None
    comfort_deviation: ComfortDeviation | None = None
    energy: EnergyThresholds | None = None
