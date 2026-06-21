"""Calendar workspace API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CalendarWorkspaceView = Literal["user", "butler", "proposals", "overlays"]
UnifiedCalendarSourceType = Literal[
    "provider_event",
    "scheduled_task",
    "butler_reminder",
    "manual_butler_event",
    "proposed_event",
    "overlay_contribution",
]
CalendarSyncState = Literal["fresh", "stale", "syncing", "failed"]


class UnifiedCalendarEntry(BaseModel):
    """Normalized calendar workspace row for user/butler views."""

    entry_id: UUID
    #: ``calendar_events.id`` for entries backed by a stored calendar event.
    #: ``None`` for entries with no underlying ``calendar_events`` row (e.g.
    #: pending proposals and overlay contributions).  This is the id the
    #: meeting-prep rail (``GET /api/calendar/workspace/prep/{event_id}``) keys
    #: on — distinct from ``entry_id`` which is the per-instance id.
    event_id: UUID | None = None
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


class CalendarProposalAcceptRequest(BaseModel):
    """Optional inline overrides applied when accepting a proposal.

    The accept endpoint reads the stored proposal payload; any field set here
    overrides the corresponding stored value before the event is created on the
    Butlers subcalendar.  An empty body (or omitted body) accepts the proposal
    exactly as stored.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    description: str | None = None
    location: str | None = None


class CalendarProposalActionResponse(BaseModel):
    """Result of an accept/dismiss action on a calendar proposal."""

    proposal_id: UUID
    status: str
    accepted_event_id: UUID | None = None
    butler_name: str | None = None


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
    #: Whether this calendar is enabled as a sync source. Toggled via
    #: ``POST /api/calendar/sources``. A disabled source is rendered "off" (not
    #: failed) and is skipped by the sync loop. Default ``True`` (opt-out).
    sync_enabled: bool = True


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
    #: Overlays lane (``view=overlays``) honest empty-state flag. ``True`` when at
    #: least one valid precomputed overlay contribution exists in range; ``False``
    #: when the cached view is absent/unreadable or no specialist has contributed.
    #: Always ``False`` for the user/butler/proposals views (additive — clients
    #: that ignore it observe the prior shape).
    has_domain_context: bool = False


class DayBriefingKindGroup(BaseModel):
    """One ``kind`` bucket inside a butler's day-briefing group.

    Holds the overlay entries of a single ``kind`` (e.g. ``bill_due``,
    ``appointment``) so the FE can render a labelled row of chips, each linking
    to the underlying domain item.
    """

    kind: str
    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)


class DayBriefingButlerGroup(BaseModel):
    """All of one specialist butler's overlay entries for the target date.

    Grouped first by ``source_butler`` then by ``kind`` so the card renders a
    section per domain (finance / travel / relationship / health) with chips
    bucketed by kind.  ``count`` is the total entries across this butler's kinds.
    """

    source_butler: str
    count: int
    kinds: list[DayBriefingKindGroup] = Field(default_factory=list)


class CalendarDayBriefingResponse(BaseModel):
    """Structured "tomorrow at a glance" day-briefing card payload.

    Assembled from the cached ``calendar.v_overlay_contributions`` view for a
    single target date — NO per-open LLM call and no generated prose.  Entries
    are grouped by butler/kind (``groups``) and also returned as a flat,
    chip-ready list (``entries``).

    Honest empty-state contract:
    - ``has_domain_context=True`` when at least one specialist wrote a
      contribution for the date (even ``has_entries=false`` → zero entries); the
      FE renders the card with whatever entries exist.
    - ``has_domain_context=False`` when no specialist contributed (jobs not run
      or the view is absent) → the FE renders "No domain context for this day"
      rather than omitting the section.

    Fail-open: a missing/unreadable view degrades to ``entries=[]`` /
    ``has_domain_context=false`` (never HTTP 500) and does NOT use the
    ``aggregates_available`` Prometheus degraded envelope.
    """

    date: str
    timezone: str
    has_domain_context: bool = False
    has_entries: bool = False
    groups: list[DayBriefingButlerGroup] = Field(default_factory=list)
    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)


class CalendarPrepNote(BaseModel):
    """A single durable relationship note surfaced on the meeting-prep rail."""

    kind: str
    text: str


class CalendarPrepAttendee(BaseModel):
    """Precomputed prep context for one resolved attendee of a selected event.

    All fields are drawn from contribution-sourced cached data (the relationship
    butler's deterministic prep job). ``dunbar_tier`` is the relationship
    letter-mark source (the FE maps the integer tier to its letter); ``notes``
    are durable CRM notes; ``last_met`` / ``last_met_event`` come from the most
    recent prior co-attended event; ``message_context`` is reserved for the
    email/message-owning butlers' contribution (empty until that lands).
    """

    entity_id: str
    name: str
    dunbar_tier: int | None = None
    notes: list[CalendarPrepNote] = Field(default_factory=list)
    last_met: str | None = None
    last_met_event: str | None = None
    message_context: list[dict[str, Any]] = Field(default_factory=list)


class CalendarPrepResponse(BaseModel):
    """Meeting-prep rail payload for GET /api/calendar/workspace/prep/{event_id}.

    Assembled exclusively from the cached ``calendar.v_prep_contributions`` view —
    NO direct ``relationship.*`` / ``health.*`` read and NO per-open LLM call.

    Honest empty-state contract:
    - ``has_prep_context=True`` when at least one contributing specialist wrote a
      prep contribution for the event; ``attendees`` carries the merged context.
    - ``has_prep_context=False`` when no prep contribution exists (co-attended /
      contact-link coverage not yet populated, jobs not run, or the view is
      absent) → the FE renders "No prep context yet" rather than an error. This
      is the expected state for most events today.

    Fail-open: a missing/unreadable view degrades to this empty-state (never
    HTTP 500) and does NOT use the ``aggregates_available`` Prometheus envelope.
    """

    event_id: str
    has_prep_context: bool = False
    attendees: list[CalendarPrepAttendee] = Field(default_factory=list)
    source_butlers: list[str] = Field(default_factory=list)


class CalendarWorkspaceSearchResponse(BaseModel):
    """Read payload for GET /api/calendar/workspace/search.

    ``entries`` are ``UnifiedCalendarEntry`` rows ranked by trigram relevance
    (highest first), each carrying the matching event instance's date(s) so the
    UI can group results by day and jump-to the event.  A blank query yields an
    empty list.

    Honest degraded contract (fail-open, never HTTP 500): ``available`` is
    ``False`` only when every calendar schema failed to respond, so ``entries``
    is empty because the search could not run — NOT because nothing matched. The
    UI must render "search unavailable" rather than a misleading "no results".
    ``available=True`` covers both real hits and a genuine empty result set.
    """

    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)
    available: bool = True


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


_FIND_TIME_WEEKDAY_CODES = frozenset({"MO", "TU", "WE", "TH", "FR", "SA", "SU"})


class CalendarFindTimeConstraints(BaseModel):
    """Structured (pre-parsed) constraints for the free-slot finder.

    Natural-language constraints ("mornings only", "avoid Fridays") are parsed
    into this structured form at the call site; the finder itself performs no LLM
    call. Both fields are soft preferences used for ranking, not hard filters.
    """

    part_of_day: Literal["morning", "afternoon", "evening"] | None = None
    avoid_weekdays: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_weekdays(self) -> CalendarFindTimeConstraints:
        normalized: list[str] = []
        for raw in self.avoid_weekdays:
            code = str(raw).strip().upper()
            if code not in _FIND_TIME_WEEKDAY_CODES:
                raise ValueError(f"Invalid weekday {raw!r}; expected iCal codes like MO, TU, …, SU")
            if code not in normalized:
                normalized.append(code)
        object.__setattr__(self, "avoid_weekdays", normalized)
        return self


class CalendarWorkspaceFindTimeRequest(BaseModel):
    """Request payload for POST /api/calendar/workspace/find-time."""

    butler_name: str
    duration_minutes: int = Field(gt=0, le=24 * 60)
    search_start: datetime
    search_end: datetime
    calendar_ids: list[str] | None = None
    constraints: CalendarFindTimeConstraints | None = None
    limit: int = Field(default=10, gt=0, le=100)

    @model_validator(mode="after")
    def _validate_window(self) -> CalendarWorkspaceFindTimeRequest:
        if self.search_end <= self.search_start:
            raise ValueError("search_end must be after search_start")
        return self


class CalendarWorkspaceFindTimeResponse(BaseModel):
    """Response payload for POST /api/calendar/workspace/find-time.

    ``slots`` are ranked open time slots (earliest-first, constraint matches
    preferred).

    Honest degraded contract (fail-open, never HTTP 500): finding time depends
    on a cross-source free/busy lookup dispatched to the calendar butler over
    MCP, which may be unreachable.

    - ``available=True`` — the free/busy lookup ran. An empty ``slots`` list then
      means the window genuinely had no gap long enough for ``duration_minutes``.
    - ``available=False`` — the lookup could not run (butler unreachable / MCP
      transport failure); ``slots`` is empty because nothing was checked, NOT
      because the calendar is open. ``reason`` carries a human-readable
      explanation and the UI must render "free/busy unavailable" rather than a
      misleading "no open slots".
    """

    slots: list[CalendarSuggestedSlot] = Field(default_factory=list)
    duration_minutes: int
    calendar_ids: list[str] = Field(default_factory=list)
    available: bool = True
    reason: str | None = None


class CalendarButlerEventPreviewRequest(BaseModel):
    """Request payload for POST /api/calendar/workspace/butler-events/preview.

    Dry-runs the recurrence expansion for a *draft* butler event so the user can
    see which dates the scheduler will actually fire before saving. Exactly one
    of ``rrule`` or ``cron`` must be supplied; nothing is persisted.
    """

    rrule: str | None = None
    cron: str | None = None
    start_at: datetime | None = None
    until_at: datetime | None = None
    timezone: str | None = None
    duration_minutes: int = Field(default=15, gt=0, le=24 * 60)
    limit: int = Field(default=6, gt=0, le=100)

    @model_validator(mode="after")
    def _validate_recurrence(self) -> CalendarButlerEventPreviewRequest:
        has_rrule = bool(self.rrule and self.rrule.strip())
        has_cron = bool(self.cron and self.cron.strip())
        if has_rrule == has_cron:
            raise ValueError("Specify exactly one of rrule or cron")
        return self


class CalendarButlerEventPreviewResponse(BaseModel):
    """Response payload for the recurrence dry-run preview.

    ``occurrences`` is the capped list (at most ``limit``) of projected start
    datetimes within the 90-day projection window. ``more_count`` counts the
    additional occurrences inside the window beyond the cap — the "+N more in
    90 days" sentinel. ``notes`` records any lossy RRULE->cron degradations the
    butler scheduler would apply (e.g. an unsupported INTERVAL).
    """

    occurrences: list[datetime] = Field(default_factory=list)
    total_in_window: int = 0
    more_count: int = 0
    window_start: datetime
    window_end: datetime
    effective_cron: str | None = None
    notes: list[str] = Field(default_factory=list)


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


class CalendarIcsImportedEvent(BaseModel):
    """One event that was created from an imported ``.ics`` payload."""

    title: str
    start_at: datetime
    all_day: bool = False


class CalendarIcsImportResponse(BaseModel):
    """Result of a ``POST /api/calendar/import/ics`` import-with-dedup run.

    ``imported`` and ``skipped_duplicates`` always sum to ``parsed`` (every
    parseable VEVENT is either created or recognised as a duplicate). Re-importing
    the same ``.ics`` yields ``imported == 0`` (a no-op) because every event
    collapses onto an existing workspace entry via the read-model collapse keys.
    """

    parsed: int
    imported: int
    skipped_duplicates: int
    imported_events: list[CalendarIcsImportedEvent] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Accounts control plane — GET /api/calendar/accounts
# ---------------------------------------------------------------------------

CalendarAccountHealthState = Literal["healthy", "degraded", "error", "unknown"]


class CalendarAccountHealth(BaseModel):
    """Per-account Google Calendar connector health.

    ``state == "unknown"`` is the graceful-degradation value used when the
    connector health surface (``switchboard.connector_registry``) is
    unavailable — the account is still returned, just without a live health
    signal. ``error_kind`` mirrors the calendar sync ``error_kind`` taxonomy so
    the UI can pick the right Recover/Reconnect CTA.
    """

    state: CalendarAccountHealthState = "unknown"
    error_kind: Literal["none", "token_expired", "auth", "not_found", "transient"] = "none"
    error_message: str | None = None
    last_heartbeat_at: datetime | None = None
    last_ingest_at: datetime | None = None


class CalendarAccountEntry(BaseModel):
    """One connected Google account with its calendar connector health."""

    account_id: UUID
    email: str | None = None
    display_name: str | None = None
    is_primary: bool = False
    status: str
    health: CalendarAccountHealth = Field(default_factory=CalendarAccountHealth)


class CalendarAccountsResponse(BaseModel):
    """Response payload for GET /api/calendar/accounts.

    ``health_available`` is ``False`` when the connector health surface could
    not be reached; accounts are still returned (with ``state="unknown"``), so
    the drawer renders the account cards in a degraded state rather than failing.
    """

    accounts: list[CalendarAccountEntry] = Field(default_factory=list)
    health_available: bool = True


# ---------------------------------------------------------------------------
# Per-calendar source enable/disable — POST /api/calendar/sources
# ---------------------------------------------------------------------------


class CalendarSourceToggleRequest(BaseModel):
    """Request payload for POST /api/calendar/sources.

    Enables or disables a single calendar as a sync source by toggling the
    ``sync_enabled`` flag on the existing ``calendar_sources`` row (no new
    table). Identify the source by ``source_key`` (or ``source_id``) within the
    owning ``butler`` schema.
    """

    butler: str
    source_key: str | None = None
    source_id: UUID | None = None
    enabled: bool

    @model_validator(mode="after")
    def _validate_target(self) -> CalendarSourceToggleRequest:
        if self.source_key is None and self.source_id is None:
            raise ValueError("Specify one of source_key or source_id")
        if self.source_key is not None and self.source_id is not None:
            raise ValueError("Specify at most one of source_key or source_id")
        return self


class CalendarSourceToggleResponse(BaseModel):
    """Response payload for POST /api/calendar/sources."""

    butler: str
    source_key: str
    source_id: UUID
    calendar_id: str | None = None
    enabled: bool


# ---------------------------------------------------------------------------
# Natural-language quick-add (parse-then-confirm)
# ---------------------------------------------------------------------------


class QuickAddParseRequest(BaseModel):
    """Request payload for POST /api/calendar/workspace/parse-quick-add.

    A parse-only request: a free-text ``text`` string is LLM-parsed into a
    draft event for confirmation. ``timezone`` (IANA, e.g. ``Asia/Singapore``)
    anchors relative phrases like "Fri 1pm". ``butler_name`` selects the
    catalog model overrides for resolution; when omitted a neutral synthetic
    name is used so the global catalog applies. ``now`` optionally pins the
    reference "now" (ISO-8601) for deterministic relative-date resolution.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    timezone: str | None = None
    butler_name: str | None = None
    now: str | None = None

    @field_validator("text")
    @classmethod
    def _require_nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text must be a non-empty string")
        return normalized

    @field_validator("timezone", "butler_name", "now")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class QuickAddDraft(BaseModel):
    """A parsed draft event — advisory only, never auto-written.

    Field names mirror the ``calendar_create_event`` create payload so the
    confirm step can submit the (possibly edited) draft to the existing
    ``POST /api/calendar/workspace/user-events`` path with minimal mapping.
    """

    title: str
    start_at: str | None = None
    end_at: str | None = None
    all_day: bool = False
    location: str | None = None
    description: str | None = None


class QuickAddParseResponse(BaseModel):
    """Response payload for POST /api/calendar/workspace/parse-quick-add.

    ``parse_available`` is ``false`` when no cheap-tier model is configured or
    the model output could not be interpreted as a single event draft; in that
    case ``draft`` is ``None`` and ``reason`` carries a human-readable
    explanation. The endpoint never fabricates an event and never writes.
    """

    parse_available: bool
    draft: QuickAddDraft | None = None
    reason: str | None = None


class CalendarDedupRulesModel(BaseModel):
    """The active cross-source dedup rules (workspace-global).

    ``match_strategy`` selects which collapse passes run:
    - ``exact`` — only the origin-ref identity pass.
    - ``balanced`` (default) — origin-ref + title/start collapse (current behavior).
    - ``aggressive`` — as ``balanced`` but strips non-alphanumerics from titles.

    ``noisy_threshold`` is the minimum cluster size (collapsed-member count) for a
    cluster to be reported on the review surface; default ``2`` reports every
    duplicate group.
    """

    match_strategy: Literal["exact", "balanced", "aggressive"] = "balanced"
    noisy_threshold: int = Field(2, ge=2, le=1000)


class CalendarDedupRulesUpdateRequest(BaseModel):
    """PATCH body for the dedup rules; omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid")

    match_strategy: Literal["exact", "balanced", "aggressive"] | None = None
    noisy_threshold: int | None = Field(None, ge=2, le=1000)


class CalendarDuplicateCluster(BaseModel):
    """One collapsed cross-source duplicate cluster surfaced for review.

    ``kept_entry`` is the survivor the read keeps (lowest keyset);
    ``duplicate_entries`` are the copies the dedup collapses away.  When
    ``keep_separate`` is true the user has pinned this cluster so the read does
    NOT collapse it (every member is shown in the workspace).
    """

    cluster_key: str
    match_pass: Literal["origin_ref", "title"]
    member_count: int
    keep_separate: bool = False
    kept_entry: UnifiedCalendarEntry
    duplicate_entries: list[UnifiedCalendarEntry]


class CalendarDuplicatesResponse(BaseModel):
    """Response for GET /api/calendar/workspace/duplicates.

    ``available`` is ``false`` only when the underlying read could not run; an
    empty ``clusters`` list with ``available=true`` genuinely means no duplicates
    were collapsed in the window.
    """

    clusters: list[CalendarDuplicateCluster]
    rules: CalendarDedupRulesModel
    available: bool = True


class CalendarKeepSeparateRequest(BaseModel):
    """Body to pin/unpin a duplicate cluster as keep-separate."""

    model_config = ConfigDict(extra="forbid")

    cluster_key: str = Field(..., min_length=1)
    keep_separate: bool
    match_pass: Literal["origin_ref", "title"] | None = None
    label: str | None = None


class CalendarKeepSeparateResponse(BaseModel):
    """Result of a keep-separate toggle."""

    cluster_key: str
    keep_separate: bool
