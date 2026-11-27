"""Calendar workspace API models."""

from __future__ import annotations

from datetime import date as LocalDate
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


class CalendarLinkedPerson(BaseModel):
    """A person linked to a calendar event via ``calendar_event_entities``.

    Carries the identity-layer ``entity_id`` plus a resolved ``display_label``
    (the person's ``public.entities.canonical_name``) so the frontend can render
    a name/avatar chip for an *existing* event without a second round-trip — the
    persistence counterpart to the creation-time ``ContactPeoplePicker`` chips
    (bu-hzi4v / PR #3020).
    """

    entity_id: str = Field(..., description="public.entities.id (UUID as string)")
    display_label: str = Field(
        ...,
        description="Resolved person label (public.entities.canonical_name), or 'Unknown'.",
    )


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
    #: The IANA zone the event was authored in (``instance_timezone`` else ``event_timezone``),
    #: kept beside ``timezone`` (the display zone the times above are converted to) so a client can
    #: show a foreign-zone event's own clock. ``None`` when the row carries no valid zone: unknown,
    #: never guessed (bu-5ii4gj.5).
    source_timezone: str | None = None
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

    #: People linked to this event via ``calendar_event_entities`` (bu-qs64f),
    #: resolved to ``entity_id`` + ``display_label`` from ``public.entities`` so
    #: existing event pills/detail panel can hydrate linked-people avatars — not
    #: just at creation time. Additive and defaults to ``[]`` (clients that ignore
    #: it observe the prior shape); populated only on the user/butler views.
    linked_people: list[CalendarLinkedPerson] = Field(default_factory=list)
    #: The event's attendees could not be read or are not resolved yet. Render "attendees
    #: unavailable", never an empty people list that reads as "no one".
    attendees_unavailable: bool = False
    #: Attendees that resolved to no one (or to more than one): a count, never named.
    unresolved_attendees: int = 0


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
    #: Whether a sync verdict applies at all. Only provider sources are synced;
    #: internal sources (schedules, reminders) are projected locally and never
    #: get a cursor, so their null ``last_synced_at`` is normal, not "never
    #: synced". Mirrors the sync deadman's provider-only rule (bu-ak7fij).
    sync_applicable: bool = True
    #: Scheduled-task calendar mirrors that could not be pushed (``failed``) or whose provider copy
    #: could not be deleted (``retiring``), for the internal scheduler source. While either is
    #: non-zero the source reports ``sync_state="failed"``. Additive.
    mirror_failed: int = 0
    mirror_retiring: int = 0
    #: Whose time this calendar's events are, and whether that was inferred or the owner's word.
    time_role: str = "unclassified"
    role_basis: Literal["provider_inferred", "owner_confirmed"] = "provider_inferred"


class CalendarWorkspaceLaneDefinition(BaseModel):
    """Butler-lane metadata used by calendar workspace layouts."""

    lane_id: str
    butler_name: str
    title: str
    source_keys: list[str] = Field(default_factory=list)


class CalendarSuppressedGroup(BaseModel):
    """A same-title group the workspace read held back from ``entries`` (bu-5ii4gj.4).

    A title that repeats more than the noise threshold on one display-timezone day is collapsed
    out of ``entries`` so a feed does not bury the grid. The group is disclosed here, with its
    entries, so the client can show a counted chip and expand it rather than read a packed day
    as complete. Counts cover the page the group was found on.
    """

    title: str
    day: str
    count: int
    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)


class OverlayFreshness(BaseModel):
    """How current one butler's overlay contribution is."""

    butler: str
    state: Literal["ok", "stale", "partial", "missing", "failed"]
    #: When the butler's job last wrote an envelope; ``None`` when it never did or the envelope
    #: predates the stamp.
    generated_at: datetime | None = None
    #: For ``stale``: the instant it went stale (``generated_at`` plus twice the cadence).
    stale_since: datetime | None = None


class CalendarWorkspaceReadResponse(BaseModel):
    """Read payload for GET /api/calendar/workspace.

    ``next_cursor`` / ``has_more`` implement keyset (cursor) pagination over the
    workspace ordering ``(starts_at, id)``: ``next_cursor`` is an opaque token
    encoding the last ``(starts_at, id)`` returned and is ``None`` on the final
    page; ``has_more`` is ``True`` while further pages remain. No ``total`` is
    computed, per the repo's keyset pagination convention.
    """

    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)
    #: Same-title groups held back from ``entries`` (see ``CalendarSuppressedGroup``). Empty
    #: when nothing was suppressed. Additive.
    suppressed_groups: list[CalendarSuppressedGroup] = Field(default_factory=list)
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
    #: Overlays lane degraded signal. ``False`` when the cached view could not be read, so the
    #: FE shows "overlays unavailable" instead of the "no domain context" empty state.
    overlays_available: bool = True
    #: Per contributing butler: ``ok``, ``stale``, ``partial``, ``missing`` or ``failed``.
    #: Empty outside the overlays lane.
    overlay_freshness: list[OverlayFreshness] = Field(default_factory=list)
    #: Proposals lane degraded signal and count of rows dropped as malformed. ``False`` when
    #: any butler schema's proposals read failed; the FE renders "Proposals unavailable", never
    #: "Nothing awaiting your review".
    proposals_available: bool = True
    proposals_dropped_rows: int = 0
    #: Linked-people resolution honesty flag (bu-qs64f). ``True`` when the
    #: ``calendar_event_entities`` → ``public.entities`` resolution ran cleanly
    #: (including "no links" — genuinely empty). ``False`` only when at least one
    #: targeted schema's resolution query FAILED, so the FE renders a "people
    #: unavailable" indicator rather than reading empty ``linked_people`` as
    #: "no one is linked". Follows the fleet degraded-source convention.
    people_source_available: bool = True
    #: Events fan-out honesty flag (bu-yjfk2). ``True`` (default) when every
    #: targeted butler schema's workspace query ran cleanly. ``False`` when at
    #: least one schema FAILED — a partial fan-out failure silently drops that
    #: schema's entries, so the FE must render a "some sources unavailable" note
    #: instead of reading a short grid as an honest "nothing scheduled". The core
    #: ``calendar_event_instances`` / ``calendar_events`` / ``calendar_sources``
    #: tables exist in every calendar butler, so a failure is genuine (never a
    #: legitimately-absent table). Always ``True`` for the proposals/overlays
    #: lanes (they do not fan out the events read). Additive — clients that ignore
    #: it observe the prior shape.
    entries_source_available: bool = True
    #: Sources fan-out honesty flag (bu-sn71y). ``True`` (default) when every targeted
    #: butler schema's ``calendar_sources`` fan-out ran cleanly. ``False`` when at least
    #: one schema FAILED — a partial failure silently drops that schema's sources from
    #: ``source_freshness``, so the FE must render a "some sources unavailable" note
    #: rather than read a shorter freshness list as a complete all-clear.
    #: ``calendar_sources`` is a core calendar table present in every calendar butler,
    #: so a failure is genuine (never a legitimately-absent table). Follows the fleet
    #: degraded-source convention. Additive — clients that ignore it observe the prior
    #: shape.
    sources_available: bool = True


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
    #: ``False`` when the overlay view could not be read (not the same as "no context").
    overlays_available: bool = True
    overlay_freshness: list[OverlayFreshness] = Field(default_factory=list)
    has_entries: bool = False
    groups: list[DayBriefingButlerGroup] = Field(default_factory=list)
    entries: list[UnifiedCalendarEntry] = Field(default_factory=list)


class CalendarPrepNote(BaseModel):
    """A single durable relationship note surfaced on the meeting-prep rail."""

    kind: str
    text: str


CalendarPrepCommitmentKind = Literal[
    "promise",
    "waiting_for",
    "follow_up",
    "obligation",
    "decision",
]
CalendarPrepCommitmentDirection = Literal["owner_to_other", "other_to_owner", "self"]
CalendarPrepCommitmentEscalationLevel = Literal["L0", "L1", "L2", "L3"]


class CalendarPrepCommitment(BaseModel):
    """An active owner commitment surfaced for a meeting attendee."""

    kind: CalendarPrepCommitmentKind
    direction: CalendarPrepCommitmentDirection
    summary: str
    deadline: str | None = None
    # The condition ledger's escalation levels are serialized as L0-L3 labels.
    escalation_level: CalendarPrepCommitmentEscalationLevel
    fingerprint: str


class CalendarPrepAttendee(BaseModel):
    """Precomputed prep context for one resolved attendee of a selected event.

    All fields are drawn from contribution-sourced cached data (the relationship
    butler's deterministic prep job). ``dunbar_tier`` is the relationship
    letter-mark source (the FE maps the integer tier to its letter); ``notes``
    are durable CRM notes; ``last_met`` / ``last_met_event`` come from the most
    recent prior co-attended event; ``message_context`` is reserved for the
    email/message-owning butlers' contribution (empty until that lands);
    ``commitments`` are active owner-condition commitments from the precomputed
    relationship contribution (empty for legacy envelopes).
    """

    entity_id: str
    name: str
    dunbar_tier: int | None = None
    notes: list[CalendarPrepNote] = Field(default_factory=list)
    last_met: str | None = None
    last_met_event: str | None = None
    # ``last_met`` derives only from a meeting somebody vouches for; the basis is
    # ``you_confirmed``, ``evidence`` or ``interim`` (accepted, not yet confirmed). A meeting
    # nobody vouches for is ``last_scheduled``, never ``last_met``.
    last_met_basis: str | None = None
    last_scheduled: str | None = None
    last_scheduled_event: str | None = None
    message_context: list[dict[str, Any]] = Field(default_factory=list)
    commitments: list[CalendarPrepCommitment] = Field(default_factory=list)


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
    #: The event's attendees could not be read or are not resolved yet (never "no one").
    attendees_unavailable: bool = False
    #: Attendees that resolved to no one (or to more than one): a count, never named.
    unresolved_attendees: int = 0


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
    #: Sources fan-out honesty flag (bu-sn71y). ``True`` (default) when every targeted
    #: butler schema's ``calendar_sources`` fan-out ran cleanly. ``False`` when at least
    #: one schema FAILED — a partial failure silently drops that schema's sources from
    #: ``connected_sources``, so the FE's freshness plaque must render "Sync status
    #: unavailable" rather than let a shorter list read as "all sources healthy".
    #: ``calendar_sources`` is a core calendar table present in every calendar butler,
    #: so a failure is genuine (never a legitimately-absent table). Follows the fleet
    #: degraded-source convention. Additive — clients that ignore it observe the prior
    #: shape.
    sources_available: bool = True


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
    """One queued sync-command acknowledgement/result."""

    butler_name: str
    source_key: str | None = None
    calendar_id: str | None = None
    status: str
    detail: str | None = None
    error: str | None = None
    #: Whether the module reports recovery completion. A queued acknowledgement
    #: leaves this false; completion is observed through action/freshness telemetry.
    recovery: bool = False
    #: Correlation id of the durable action-log command. May differ from the
    #: outer request when an equivalent pending command was coalesced.
    request_id: str | None = None
    #: ``True`` when this acknowledgement joined existing queue work instead of
    #: creating a new pending command.
    coalesced: bool = False


class CalendarWorkspaceSyncResponse(BaseModel):
    """Response payload for POST /api/calendar/workspace/sync."""

    scope: Literal["all", "source"]
    requested_source_key: str | None = None
    requested_source_id: UUID | None = None
    #: Correlation id generated for this dashboard/API request.
    request_id: str
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


# ---------------------------------------------------------------------------
# Conflict & overcommitment radar — GET /api/calendar/workspace/conflicts
# ---------------------------------------------------------------------------

ConflictKind = Literal["overlap", "back_to_back", "overloaded_day", "outside_declared_hours"]
ConflictSeverity = Literal["info", "warning"]


class ConflictEventRef(BaseModel):
    """An event contributing to a detected conflict/overcommitment issue."""

    entry_id: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str  # "confirmed" | "tentative"


class ResolutionOption(BaseModel):
    """One way to finish an overlap: an exact call and who has to say yes (bu-5ii4gj.11)."""

    id: str
    kind: Literal["move_block", "move_own", "decline", "tentative", "ask_organizer", "keep_both"]
    authority: Literal["no_park_undo", "owner_submit", "parked_per_event"]
    #: ``{"tool": str, "args": dict}``: exactly what the resolve route would run.
    exact_call: dict[str, Any]
    #: Which fields explain the option (entry ids, the chosen slot); never event text.
    rationale_fields: dict[str, Any] = Field(default_factory=dict)


class ConflictResolveRequest(BaseModel):
    """Resolve one overlap by one of the options the scan offered (bu-5ii4gj.11)."""

    pair_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)
    #: The digest the owner saw. A moved overlap answers 409 instead of acting on a stale read.
    issue_digest: str = Field(min_length=1)
    start: datetime
    end: datetime


class ConflictResolveResponse(BaseModel):
    status: Literal["acknowledged", "already_acknowledged", "parked"]
    pair_id: str
    option_id: str
    #: The approvals card a move was parked as; the owner decides it in the approvals surface.
    action_id: UUID | None = None


class ConflictIssue(BaseModel):
    """A single scheduling problem detected in the scanned window."""

    kind: ConflictKind
    #: Calendar date (YYYY-MM-DD) the issue falls on, in the display timezone.
    date: str
    summary: str
    severity: ConflictSeverity
    events: list[ConflictEventRef] = Field(default_factory=list)
    #: UUIDs of any ``pending`` fix proposals matching this issue (empty until
    #: the LLM fix-proposal session has run for the window).
    proposal_ids: list[str] = Field(default_factory=list)
    #: Overlap issues only: resolution options and the digest a resolve must echo back. Empty with
    #: ``options_reason='stale_source'`` when a calendar source is too stale to offer a move.
    options: list[ResolutionOption] = Field(default_factory=list)
    issue_digest: str | None = None
    options_reason: str | None = None
    #: ``outside_declared_hours`` only: which declared limit the event breaks (``before_earliest``,
    #: ``after_latest``, ``off_day`` or ``no_meeting_block``). Empty for every other kind.
    reasons: list[str] = Field(default_factory=list)


class ConflictScanWindow(BaseModel):
    """The requested scan window echoed back in the response."""

    start: datetime
    end: datetime


class HouseholdEventRef(BaseModel):
    """One other-person event: when, whose and nothing else (REQ-module-calendar-038).

    Deliberately has no title, location, description or attendee field: another person's text
    must not reach a response, a prompt or a context line.
    """

    role: Literal["household_member", "other_person", "team"]
    subject_entity_id: UUID | None = None
    subject_label: str | None = None
    start_at: datetime
    end_at: datetime
    all_day: bool = False


class HouseholdSubjectGroup(BaseModel):
    """One subject's context events (``subject_entity_id`` null for an unattributed source)."""

    subject_entity_id: UUID | None = None
    subject_label: str | None = None
    events: list[HouseholdEventRef] = Field(default_factory=list)


class HouseholdTimeResponse(BaseModel):
    """Response payload for GET /api/calendar/workspace/household-time.

    Context only: nothing here counts toward owner load. ``available=False`` means the read
    could not run, so an empty list is not "nobody has anything on".
    """

    window: ConflictScanWindow
    subjects: list[HouseholdSubjectGroup] = Field(default_factory=list)
    available: bool = True


class CalendarChange(BaseModel):
    """One organizer-made change to an event the owner planned around (bu-5ii4gj.11 N3)."""

    event_id: str
    calendar_id: str
    #: The calendar entry to open as evidence; ``None`` when the projection has not seen the event.
    entry_id: UUID | None = None
    #: From the projection, never the journal; ``None`` when the event is not projected.
    title: str | None = None
    observed_at: datetime
    prior_known: bool
    all_day: bool = False
    #: ``time``, ``status`` and/or ``location``; empty when no prior version was journaled.
    fields: list[str] = Field(default_factory=list)
    prior_start_at: datetime | None = None
    new_start_at: datetime | None = None
    prior_end_at: datetime | None = None
    new_end_at: datetime | None = None
    prior_status: str | None = None
    new_status: str | None = None
    prior_location: str | None = None
    new_location: str | None = None
    #: ``False`` when the organizer is someone else; ``None`` ("organizer unknown") when the
    #: journal row keeps structure only. A change the owner made themselves is never returned.
    organizer_self: bool | None = None


class CalendarChangesResponse(BaseModel):
    """Response payload for GET /api/calendar/workspace/changes.

    ``changes_available=False`` means the journal read failed: ``changes`` is empty because nothing
    was read, which the queue must show as unknown, never as no changes.
    """

    changes: list[CalendarChange] = Field(default_factory=list)
    changes_available: bool = True
    #: ``True`` when more changes exist than were returned.
    truncated: bool = False
    #: ``False`` when the owner's account addresses could not be read (an upper bound:
    #: the list may include the owner's own edits).
    owners_complete: bool = True
    since: datetime


class ConflictScanResponse(BaseModel):
    """Response payload for GET /api/calendar/workspace/conflicts.

    Honest degraded contract (fail-open, never HTTP 500): the radar reads the
    synced ``calendar_event_instances`` / ``calendar_events`` tables via a
    cross-schema fan-out that may fail.

    - ``issues_available=True`` — the scan ran. An empty ``issues`` list then
      means the window genuinely had no overlaps, dense chains, or overloaded
      days.
    - ``issues_available=False`` — the scan could not run (DB fan-out failure);
      ``issues`` is empty because nothing was checked. The FE renders no banner
      and adds no amber edges in this silent degraded mode.
    """

    issues: list[ConflictIssue] = Field(default_factory=list)
    scan_window: ConflictScanWindow
    issues_available: bool = True
    #: Calendars (and their events) the scan skipped because their source role is unclassified
    #: and the owner is not invited. Non-zero means "not everything was checked", not "clear".
    unclassified_sources: int = 0
    unclassified_events: int = 0
    #: Other people's events in the window: attributed, opaque, never counted as owner load.
    household_context: list[HouseholdEventRef] = Field(default_factory=list)


class CalendarInvitationEntry(BaseModel):
    """One explicit unanswered provider occurrence; no RSVP/mutation authority."""

    entry_id: UUID
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    organizer: str | None = None
    organizer_source: Literal["event", "attendee", "unknown"] = "unknown"
    butler_name: str | None = None
    conflict_issues: list[ConflictIssue] = Field(default_factory=list)
    response_configured: bool = False


class CalendarQuarantinedInvitation(BaseModel):
    """An unanswered invitation from an untrusted organizer: time and verdict only.

    Deliberately carries no title, description, location, organizer or attendee: third-party text
    is data, and the owner decides about the sender, not the content (REQ-module-calendar-041).
    """

    entry_id: UUID
    start_at: datetime
    end_at: datetime
    timezone: str
    verdict: Literal["first_contact", "blocked", "unknown"]
    lure_badges: list[str] = Field(default_factory=list)


class CalendarInvitationsResponse(BaseModel):
    entries: list[CalendarInvitationEntry] = Field(default_factory=list)
    quarantined: list[CalendarQuarantinedInvitation] = Field(default_factory=list)
    issues_available: bool = True
    conflicts_available: bool = True
    sources_degraded: list[str] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None


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
    #: Audit fan-out honesty flag. ``True`` (default) when every targeted
    #: calendar schema's ``calendar_action_log`` fan-out ran cleanly. ``False``
    #: when at least one targeted schema FAILED — a partial failure silently
    #: drops that schema's mutations AND undercounts ``total``, so the FE must
    #: render a "some sources unavailable" note rather than let the log read as a
    #: complete, shorter history. ``calendar_action_log`` is a core calendar
    #: table present in every calendar butler, so a failure is genuine (never a
    #: legitimately-absent table). Follows the fleet degraded-source convention.
    sources_available: bool = True


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


CalendarSourceRole = Literal[
    "owner",
    "household_member",
    "other_person",
    "team",
    "subscription",
    "holidays",
    "birthdays",
    "butler",
    "unclassified",
]


class CalendarSourceRoleRequest(BaseModel):
    """Request payload for POST /api/calendar/sources/role.

    ``role`` is a member of the closed role set, or ``null`` to clear the owner's confirmation.
    """

    source_key: str
    role: CalendarSourceRole | None
    #: Whose calendar it is, for ``household_member``, ``other_person`` and ``team`` only.
    subject_entity_id: UUID | None = None


class CalendarSourceRoleResponse(BaseModel):
    source_key: str
    role: CalendarSourceRole
    role_basis: Literal["provider_inferred", "owner_confirmed"]
    subject_entity_id: UUID | None = None
    updated_butlers: list[str] = Field(default_factory=list)
    #: Schemas whose write failed. Non-empty means the role is not yet the same everywhere.
    failed_butlers: list[str] = Field(default_factory=list)


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


class CalendarRespondRequest(BaseModel):
    """Public response inputs; all authority and provider bindings are server-derived."""

    model_config = ConfigDict(extra="forbid")
    entry_id: UUID
    request_id: UUID
    response_status: Literal["accepted", "declined", "tentative"]
    send_updates: Literal["none", "all", "externalOnly"] = "none"


class CalendarOrganizerDecisionRequest(BaseModel):
    """Vouch for or Block the organizer of one stored invitation (REQ-module-calendar-042).

    Only the entry id and the verb come from the caller; the organizer address is read from the
    stored event, never from the request.
    """

    model_config = ConfigDict(extra="forbid")
    entry_id: UUID
    verb: Literal["vouch", "block"]


class CalendarOrganizerDecisionResponse(BaseModel):
    verb: Literal["vouch", "block"]
    endpoints: int
    rules_written: int
    rules_removed: int
    events_restamped: int
    restamp_degraded: bool = False


class CalendarRespondResponse(BaseModel):
    status: Literal[
        "pending_approval", "approved", "rejected", "uncertain", "noop", "applied", "failed"
    ]
    command_id: UUID | None = None
    approval_id: UUID | None = None
    projection_available: bool = False
    source_butler: str
    reason: str | None = None


class DaySheetItem(BaseModel):
    ref: str
    start: datetime
    end: datetime
    all_day: bool
    kind: Literal["attended", "tentative", "butler"]
    title: str
    label_class: Literal["owner_authored", "third_party"]
    people_count: int
    conflict_ref: str | None = None
    door: str


class DaySheetGap(BaseModel):
    start: datetime
    end: datetime


class DaySheetFreshness(BaseModel):
    state: Literal["current", "stale", "unreadable"]
    as_of: datetime | None = None
    sources_unreadable: list[str] = Field(default_factory=list)
    sources_total: int = 0


class DayVerdictEntry(BaseModel):
    """The meeting a verdict clause points at; ``door`` opens it in the calendar."""

    ref: str
    start: datetime
    end: datetime
    title: str
    people_count: int
    door: str


class DayVerdictWindow(BaseModel):
    start: datetime
    end: datetime
    minutes: int


class DayVerdictResponse(BaseModel):
    """GET /api/calendar/workspace/day-verdict: a few deterministic facts about one local day.

    ``available`` is ``False`` when the calendar could not be read, and then no other fact may be
    shown as true. ``empty`` is the one case "nothing on" is earned: readable, current, no items.
    ``free_window`` is withheld when the read is stale. No model composes any field.
    """

    available: bool
    date: LocalDate
    zone: str
    #: ``today`` or ``tomorrow`` in the owner's zone, else ``other``; the page labels by ``date``.
    relative: Literal["today", "tomorrow", "other"]
    freshness: DaySheetFreshness
    current: DayVerdictEntry | None = None
    next: DayVerdictEntry | None = None
    committed_minutes: int = 0
    committed_count: int = 0
    tentative_count: int = 0
    free_window: DayVerdictWindow | None = None
    empty: bool = False


class DaySheetResponse(BaseModel):
    """GET /api/calendar/workspace/day-sheet: the one projection of an owner-local day.

    ``freshness.state`` is never inferred from an empty ``items`` list: ``unreadable`` and
    ``stale`` are explicit, and ``text`` is the fixed-template rendering the nightly message uses.
    """

    date: LocalDate
    zone: str
    freshness: DaySheetFreshness
    items: list[DaySheetItem] = Field(default_factory=list)
    gaps: list[DaySheetGap] = Field(default_factory=list)
    attended_minutes: int = 0
    attended_count: int = 0
    longest_gap: DaySheetGap | None = None
    digest: str
    text: str
