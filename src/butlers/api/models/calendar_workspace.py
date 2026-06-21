"""Calendar workspace API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

CalendarWorkspaceView = Literal["user", "butler", "proposals"]
UnifiedCalendarSourceType = Literal[
    "provider_event",
    "scheduled_task",
    "butler_reminder",
    "manual_butler_event",
    "proposed_event",
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

    # core_076 provenance columns — which butler session wrote this event
    source_butler: str | None = None
    source_session_id: str | None = None


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
    #: Coarse classification of ``last_error`` so the workspace can pick the
    #: right recovery CTA (Recover vs Reconnect). Additive: clients that ignore
    #: it observe the prior shape. ``none`` means the source is healthy.
    error_kind: Literal["none", "token_expired", "auth", "not_found", "transient"] = "none"


class CalendarWorkspaceLaneDefinition(BaseModel):
    """Butler-lane metadata used by calendar workspace layouts."""

    lane_id: str
    butler_name: str
    title: str
    source_keys: list[str] = Field(default_factory=list)


class CalendarWorkspaceReadResponse(BaseModel):
    """Read payload for GET /api/calendar/workspace.

    ``next_cursor`` / ``has_more`` implement keyset (cursor) pagination over the
    workspace ordering ``(starts_at, id)``: ``next_cursor`` is an opaque token
    encoding the last ``(starts_at, id)`` returned and is ``None`` on the final
    page; ``has_more`` is ``True`` while further pages remain. No ``total`` is
    computed, per the repo's keyset pagination convention.
    """

    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)
    source_freshness: list[CalendarWorkspaceSourceFreshness] = Field(default_factory=list)
    lanes: list[CalendarWorkspaceLaneDefinition] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


class CalendarWorkspaceSearchResponse(BaseModel):
    """Read payload for GET /api/calendar/workspace/search.

    ``entries`` are ``UnifiedCalendarEntry`` rows ranked by trigram relevance
    (highest first), each carrying the matching event instance's date(s) so the
    UI can group results by day and jump-to the event.  A blank query yields an
    empty list.
    """

    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)


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
    primary_calendar_id: str | None = None


class CalendarWorkspaceSyncRequest(BaseModel):
    """Request payload for POST /api/calendar/workspace/sync."""

    all: bool = False
    source_key: str | None = None
    source_id: UUID | None = None
    butler: str | None = None
    #: Operator-driven cursor recovery. When true, the targeted source(s) run a
    #: full re-sync (``calendar_force_sync(full=true)``) ignoring the stored
    #: incremental token. Default false preserves incremental behavior.
    full: bool = False

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
    #: Whether a full re-sync (cursor recovery) ran for this target. Mirrors the
    #: ``recovery`` flag returned by ``calendar_force_sync``.
    recovery: bool = False


class CalendarWorkspaceSyncResponse(BaseModel):
    """Response payload for POST /api/calendar/workspace/sync."""

    scope: Literal["all", "source"]
    requested_source_key: str | None = None
    requested_source_id: UUID | None = None
    #: Echoes whether the request asked for a full recovery sync.
    full: bool = False
    targets: list[CalendarWorkspaceSyncTarget] = Field(default_factory=list)
    triggered_count: int = 0


class SetPrimaryCalendarRequest(BaseModel):
    """Request payload for PUT /api/calendar/workspace/primary."""

    butler_name: str
    calendar_id: str


class SetPrimaryCalendarResponse(BaseModel):
    """Response payload for PUT /api/calendar/workspace/primary."""

    old_calendar_id: str | None = None
    new_calendar_id: str
    persisted: bool = False


class CalendarConflictEntry(BaseModel):
    """A conflicting calendar event returned by a mutation conflict check."""

    event_id: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str


class CalendarSuggestedSlot(BaseModel):
    """A suggested alternative time slot returned alongside a conflict response."""

    start_at: datetime
    end_at: datetime
    timezone: str


class CalendarWorkspaceMutationResponse(BaseModel):
    """Typed response payload for calendar workspace mutation endpoints.

    Surfaces ``conflicts`` and ``suggested_slots`` as first-class typed fields
    so the frontend can render the conflict-resolution UX without digging into
    the opaque ``result`` dict.  The raw MCP ``result`` is still included for
    backward compatibility and diagnostic purposes.
    """

    action: str
    tool_name: str
    request_id: str | None = None
    result: dict[str, Any]
    conflicts: list[CalendarConflictEntry] = Field(default_factory=list)
    suggested_slots: list[CalendarSuggestedSlot] = Field(default_factory=list)
    projection_version: str | None = None
    staleness_ms: int | None = None
    projection_freshness: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Audit trail models — GET /api/calendar/workspace/audit
# ---------------------------------------------------------------------------

CalendarActionStatus = Literal["pending", "applied", "failed", "noop"]


class CalendarAuditEntry(BaseModel):
    """One row from ``calendar_action_log``, enriched with provenance from ``calendar_events``.

    ``source_butler`` and ``source_session_id`` come from the associated
    ``calendar_events`` row (core_076 columns) via the ``event_id`` FK.
    They are ``None`` when the action has no linked event (e.g. a failed create).
    """

    id: UUID
    idempotency_key: str
    request_id: str | None = None
    action_type: str
    action_status: CalendarActionStatus
    origin_ref: str | None = None
    # Condensed payload summary — key fields only (not the full JSONB)
    payload_summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    applied_at: datetime | None = None
    # Provenance from calendar_events (core_076 source columns)
    source_butler: str | None = None
    source_session_id: str | None = None


class CalendarAuditResponse(BaseModel):
    """Response payload for GET /api/calendar/workspace/audit."""

    entries: list[CalendarAuditEntry] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50


# ---------------------------------------------------------------------------
# Undo model — POST /api/calendar/workspace/undo/{action_id}
# ---------------------------------------------------------------------------


class CalendarUndoResponse(BaseModel):
    """Response payload for POST /api/calendar/workspace/undo/{action_id}.

    Reports the original action that was reversed, the inverse calendar MCP
    tool dispatched to reverse it, the freshly generated ``request_id`` carried
    by that dispatch (so the undo is itself idempotent and audited), and the
    raw inverse mutation result.  ``undone`` is ``True`` only when the inverse
    dispatch succeeded and the original action was marked undone.
    """

    action_id: UUID
    action_type: str
    inverse_tool: str
    request_id: str
    undone: bool
    result: dict[str, Any] = Field(default_factory=dict)
