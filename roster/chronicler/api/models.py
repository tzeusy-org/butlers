"""Pydantic models for Chronicler dashboard API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

# ── Aggregate models ───────────────────────────────────────────────────────


class SourceBreakdownEntry(BaseModel):
    """Per-source contribution within an aggregate bucket."""

    source_name: str
    total_seconds: float
    episode_count: int
    tombstoned: bool = False


class CategoryBucket(BaseModel):
    """One category bucket from GET /api/chronicler/aggregate/by-category."""

    category: str
    """Activity lane (one of ``aggregations.LANES``). Only activity-layer
    episodes are counted; intent (calendar) and evidence rows never appear."""
    total_seconds: float
    episode_count: int
    source_breakdown: list[SourceBreakdownEntry] = Field(default_factory=list)
    precision: str
    """Least-precise precision value across contributing rows."""
    retention_floor_days: int | None = None
    """Shortest non-NULL retention_days across contributing rows, or None."""


class CategoryBuckets(BaseModel):
    """Response envelope for GET /api/chronicler/aggregate/by-category."""

    start_at: datetime
    end_at: datetime
    tz: str
    buckets: list[CategoryBucket] = Field(default_factory=list)
    """Sorted by total_seconds DESC, then category ASC."""


class AggregateByDayRow(BaseModel):
    """One (day, category) bucket from GET /api/chronicler/aggregate/by-day."""

    day: str
    """ISO-8601 date string for the bucket's calendar day (YYYY-MM-DD)."""
    category: str
    total_seconds: float
    episode_count: int
    day_start: datetime
    """Inclusive start of the calendar day in the requested timezone."""
    day_end: datetime
    """Exclusive end of the calendar day in the requested timezone."""
    source_breakdown: list[SourceBreakdownEntry] = Field(default_factory=list)
    precision: str
    """Least-precise precision value across contributing rows."""
    retention_floor_days: int | None = None
    """Shortest non-NULL retention_days across contributing rows, or None."""


class SubsourceCheckpoint(BaseModel):
    """Per-subsource projection checkpoint detail."""

    subsource: str
    last_run_at: datetime | None = None
    last_error: str | None = None


class ProjectionHealthRow(BaseModel):
    """Projection health for a single (source_name, subsource) checkpoint row.

    Exposed via GET /api/chronicler/projection-health to surface ingestion
    errors and watermark state without requiring DB access.
    """

    source_name: str
    subsource: str
    last_error: str | None = None
    last_run_at: datetime | None = None
    rows_projected: int
    watermark: datetime | None = None


class SourceStateRow(BaseModel):
    """Runtime state for a single source adapter, joined with projection checkpoints."""

    source_name: str
    chronicler_compatibility: str
    read_surface: str | None = None
    boundary_semantics: str | None = None
    optional_schema: bool
    active: bool
    inactive_reason: str | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None
    subsource_checkpoints: list[SubsourceCheckpoint] | None = None


class ChroniclerPointEvent(BaseModel):
    id: str
    source_name: str
    source_ref: str
    event_type: str
    occurred_at: datetime
    precision: str
    title: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    privacy: str
    retention_days: int | None = None
    tombstone_at: datetime | None = None
    canonical_occurred_at: datetime
    canonical_title: str | None = None
    canonical_privacy: str
    corrected_at: datetime | None = None
    correction_note: str | None = None
    created_at: datetime
    updated_at: datetime


class ChroniclerEpisode(BaseModel):
    id: str
    source_name: str
    source_ref: str
    episode_type: str
    start_at: datetime
    end_at: datetime | None = None
    precision: str
    title: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    privacy: str
    retention_days: int | None = None
    tombstone_at: datetime | None = None
    canonical_start_at: datetime
    canonical_end_at: datetime | None = None
    canonical_title: str | None = None
    canonical_privacy: str
    corrected_at: datetime | None = None
    correction_note: str | None = None
    created_at: datetime
    updated_at: datetime
    category: str
    """Life-balance Activity lane derived from (source_name, episode_type) by
    ``lane_for_category(category_for(...))``. One of the values in ``LANES``
    (``work``, ``play``, ``eat``, ``rest``, ``exercise``, ``travel``, ``sleep``,
    ``social``) or ``other`` when the source/type pair has no lane (e.g. an
    unmapped source, or a calendar/intent episode)."""
    participant_entity_ids: list[str] = Field(default_factory=list)
    """UUIDs of all entities linked to this episode via episode_entities join table.
    Ordered by role precedence (owner > organizer > participant) then entity_id ASC.
    Empty list when no entity links exist."""


class ChroniclerOverride(BaseModel):
    id: str
    target_kind: str
    target_id: str
    corrected_start_at: datetime | None = None
    corrected_end_at: datetime | None = None
    corrected_title: str | None = None
    corrected_privacy: str | None = None
    corrected_tombstone_at: datetime | None = None
    note: str | None = None
    submitted_by: str
    created_at: datetime


class SubmitCorrectionRequest(BaseModel):
    corrected_start_at: datetime | None = None
    corrected_end_at: datetime | None = None
    corrected_title: str | None = None
    corrected_privacy: str | None = Field(
        default=None,
        description="One of 'normal', 'sensitive', 'restricted'",
    )
    corrected_tombstone_at: datetime | None = None
    note: str | None = None
    submitted_by: str = "user"


class DayCloseFreshResponse(BaseModel):
    """Cache hit: fresh prose with provenance."""

    prose: str
    provenance_refs: list[str]
    cache_built_at: datetime


class DayCloseStaleResponse(BaseModel):
    """Cache stale: one or more source rows changed after cache_built_at."""

    stale: bool = True
    cache_built_at: datetime
    last_invalidating_event_at: datetime


class DayCloseRefreshRequest(BaseModel):
    """Request body for POST /aggregate/day-close/refresh."""

    date: date
    """YYYY-MM-DD date to refresh the day-close cache for."""
    tz: str = "UTC"
    """IANA timezone for the request (validated; default UTC).

    Note: the current implementation computes the day window in UTC regardless of
    this value.  The field is accepted and validated so the API contract is stable
    for future per-timezone cache support.
    """


class DayCloseRefreshResponse(BaseModel):
    """Response body for a successful day-close refresh."""

    cache_key: str
    cache_built_at: datetime


class EpisodeExplainResponse(BaseModel):
    """Response body for a successful per-episode explain."""

    episode_id: str
    cache_key: str
    cache_built_at: datetime


class OpsSessionRow(BaseModel):
    """One operational session row from GET /api/chronicler/ops/sessions.

    Operational sessions are those whose ``trigger_source`` matches the
    exclusion list in ``CoreSessionsAdapter`` (tick, qa, healing, schedule:*).
    They are never projected into the ``episodes`` table, so this endpoint
    is the only way to audit them via the Chronicler API.
    """

    butler: str
    """Butler schema from which this session was read."""
    session_id: str
    trigger_source: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    success: bool | None = None
    model: str | None = None


# ── Editorial briefing / attention / KPI models (bu-i29ix) ─────────────────


class ChroniclesAttentionItem(BaseModel):
    """One entry in the Chronicles attention list.

    The ``kind`` discriminates the source of the item: ``anomaly`` (sleep,
    waking gap), ``source_health`` (adapter degradation), ``open_correction``
    (unresolved overrides). Severity drives the mark-column glyph in the
    editorial attention list primitive.
    """

    kind: str
    """One of 'anomaly', 'source_health', 'open_correction'."""
    severity: str
    """One of 'high', 'medium', 'low'."""
    title: str
    detail: str | None = None
    action_href: str | None = None


class ChroniclesLaneHours(BaseModel):
    """One entry in the KPI hours-by-lane list."""

    lane: str
    """One of the ten taxonomy categories."""
    hours: float


class ChroniclesStreaks(BaseModel):
    """Small streak counters surfaced in the KPI strip."""

    sleep: int = 0
    """Consecutive days with a non-zero sleep_episode."""
    exercise: int = 0
    """Consecutive days with a non-zero workout_episode."""


class ChroniclesKpi(BaseModel):
    """KPI snapshot for a single day window."""

    hours_by_top_lanes: list[ChroniclesLaneHours] = Field(default_factory=list)
    """Top three lanes by total minutes, descending."""
    longest_episode_minutes: int = 0
    longest_episode_title: str | None = None
    longest_gap_minutes: int = 0
    """Longest gap between consecutive episodes during waking hours."""
    sleep_minutes: int = 0
    streaks: ChroniclesStreaks = Field(default_factory=ChroniclesStreaks)


class ChroniclesRecentDay(BaseModel):
    """One day in the recent-days index."""

    date: str
    """ISO calendar date (YYYY-MM-DD)."""
    total_minutes: int
    top_lane: str | None = None
    episode_count: int


class ChroniclesBriefing(BaseModel):
    """Editorial briefing object for /api/chronicler/briefing."""

    date: str
    """ISO calendar date (YYYY-MM-DD)."""
    state_class: str
    """One of 'urgent', 'busy', 'mild', 'quiet'."""
    headline: str
    """Templated, sentence case, no exclamation, no em-dash."""
    voice_paragraph: str
    """Sourced from chronicler.tier2_cache when fresh; templated otherwise."""
    voice_source: str
    """One of 'llm·cached', 'templated', 'stale'."""
    kpi: ChroniclesKpi = Field(default_factory=ChroniclesKpi)
    attention_items: list[ChroniclesAttentionItem] = Field(default_factory=list)
    recent_days: list[ChroniclesRecentDay] = Field(default_factory=list)
    earliest_date: str | None = None
    """Earliest chronicled calendar day (owner tz, YYYY-MM-DD), or null when
    no episodes exist. Bounds backward archive navigation."""


__all__ = [
    "AggregateByDayRow",
    "CategoryBucket",
    "CategoryBuckets",
    "ChroniclerEpisode",
    "ChroniclerOverride",
    "ChroniclerPointEvent",
    "ChroniclesAttentionItem",
    "ChroniclesBriefing",
    "ChroniclesKpi",
    "ChroniclesLaneHours",
    "ChroniclesRecentDay",
    "ChroniclesStreaks",
    "DayCloseRefreshRequest",
    "DayCloseRefreshResponse",
    "DayCloseFreshResponse",
    "DayCloseStaleResponse",
    "EpisodeExplainResponse",
    "OpsSessionRow",
    "ProjectionHealthRow",
    "SourceBreakdownEntry",
    "SourceStateRow",
    "SubsourceCheckpoint",
    "SubmitCorrectionRequest",
]
