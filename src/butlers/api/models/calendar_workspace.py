"""Calendar workspace API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

CalendarWorkspaceView = Literal["user", "butler"]
UnifiedCalendarSourceType = Literal[
    "provider_event",
    "scheduled_task",
    "butler_reminder",
    "manual_butler_event",
]
CalendarSyncState = Literal["fresh", "stale", "syncing", "failed"]


class UnifiedCalendarEntry(BaseModel):
    """Normalized calendar workspace row for user/butler views."""

    entry_id: UUID
    view: CalendarWorkspaceView
    source_type: UnifiedCalendarSourceType
    source_key: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    all_day: bool = False

    calendar_id: str | None = None
    provider_event_id: str | None = None
    butler_name: str | None = None
    schedule_id: UUID | None = None
    reminder_id: UUID | None = None
    rrule: str | None = None
    cron: str | None = None
    until_at: datetime | None = None
    status: str = "active"
    sync_state: CalendarSyncState | None = None
    editable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CalendarWorkspaceSourceFreshness(BaseModel):
    """Per-source freshness metadata for workspace rendering."""

    source_id: UUID
    source_key: str
    source_kind: str
    lane: CalendarWorkspaceView
    provider: str | None = None
    calendar_id: str | None = None
    butler_name: str | None = None
    display_name: str | None = None
    writable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    cursor_name: str | None = None
    last_synced_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    full_sync_required: bool = False
    sync_state: CalendarSyncState = "stale"
    staleness_ms: int | None = None


class CalendarWorkspaceLaneDefinition(BaseModel):
    """Butler-lane metadata used by calendar workspace layouts."""

    lane_id: str
    butler_name: str
    title: str
    source_keys: list[str] = Field(default_factory=list)


class CalendarWorkspaceReadResponse(BaseModel):
    """Read payload for GET /api/calendar/workspace."""

    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)
    source_freshness: list[CalendarWorkspaceSourceFreshness] = Field(default_factory=list)
    lanes: list[CalendarWorkspaceLaneDefinition] = Field(default_factory=list)


class CalendarWorkspaceCapabilitiesSync(BaseModel):
    """Sync capabilities for the calendar workspace."""

    global_: bool = Field(default=True, alias="global")
    by_source: bool = True


class CalendarWorkspaceCapabilities(BaseModel):
    """Top-level capability switches for workspace UX."""

    views: list[CalendarWorkspaceView] = Field(default_factory=lambda: ["user", "butler"])
    filters: dict[str, bool] = Field(
        default_factory=lambda: {
            "butlers": True,
            "sources": True,
            "timezone": True,
        }
    )
    sync: CalendarWorkspaceCapabilitiesSync = Field(
        default_factory=CalendarWorkspaceCapabilitiesSync
    )


class CalendarWorkspaceWritableCalendar(BaseModel):
    """Writable provider calendar descriptor for user-view create/edit flows."""

    source_key: str
    provider: str | None = None
    calendar_id: str
    display_name: str | None = None
    butler_name: str | None = None


class CalendarWorkspaceMetaResponse(BaseModel):
    """Payload for GET /api/calendar/workspace/meta."""

    capabilities: CalendarWorkspaceCapabilities = Field(
        default_factory=CalendarWorkspaceCapabilities
    )
    connected_sources: list[CalendarWorkspaceSourceFreshness] = Field(default_factory=list)
    writable_calendars: list[CalendarWorkspaceWritableCalendar] = Field(default_factory=list)
    lane_definitions: list[CalendarWorkspaceLaneDefinition] = Field(default_factory=list)
    default_timezone: str = "UTC"


class CalendarWorkspaceSyncRequest(BaseModel):
    """Request payload for POST /api/calendar/workspace/sync."""

    all: bool = False
    source_key: str | None = None
    source_id: UUID | None = None
    butler: str | None = None

    @model_validator(mode="after")
    def _validate_scope(self) -> CalendarWorkspaceSyncRequest:
        if self.all and (self.source_key is not None or self.source_id is not None):
            raise ValueError("all=true cannot be combined with source filters")
        if self.source_key is not None and self.source_id is not None:
            raise ValueError("Specify at most one of source_key or source_id")
        return self


class CalendarWorkspaceSyncTarget(BaseModel):
    """One sync trigger attempt target/result."""

    butler_name: str
    source_key: str | None = None
    calendar_id: str | None = None
    status: str
    detail: str | None = None
    error: str | None = None


class CalendarWorkspaceSyncResponse(BaseModel):
    """Response payload for POST /api/calendar/workspace/sync."""

    scope: Literal["all", "source"]
    requested_source_key: str | None = None
    requested_source_id: UUID | None = None
    targets: list[CalendarWorkspaceSyncTarget] = Field(default_factory=list)
    triggered_count: int = 0
