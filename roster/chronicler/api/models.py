"""Pydantic models for Chronicler dashboard API."""

from __future__ import annotations

from datetime import date, datetime, time
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
    low_confidence_seconds: float = 0.0
    """Of ``total_seconds``, how much is contributed by ``confidence='low'``
    activity episodes — the share the dashboard flags as needing confirmation.
    Computed as the union of the lane's low-confidence intervals, so it never
    exceeds ``total_seconds``."""
    low_confidence_episode_count: int = 0
    """Count of contributing episodes whose ``confidence='low'``."""
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
    untracked_seconds: float = 0.0
    """Waking-window seconds (owner-tz, per aggregations.untracked_seconds_for_window)
    not covered by any activity-layer episode of any lane. Lets the pie chart
    render an honest 'untracked' slice instead of renormalising over tracked
    evidence only (bu-whhll.13) — a 4h-evidence day no longer renders as a
    full day."""


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


# ── Activity evidence chain (IEA, tasks.md S9a) ────────────────────────────


class EvidenceChainLink(BaseModel):
    """One corroborating signal backing an activity, resolved from the canonical
    ``episode_event_links`` chain to its underlying point-event.

    Lets a client answer "why is this activity counted?" by listing each linked
    evidence point-event with its source and a human-readable descriptor.
    """

    event_id: str
    source_name: str
    event_type: str
    occurred_at: datetime
    relation: str
    """How the point-event relates to the activity (``supports``,
    ``boundary_start``, ``boundary_end``, ``evidence``)."""
    descriptor: str
    """Human-readable label — the event title when present, else a
    ``"{source_name} {event_type}"`` fallback."""
    privacy: str


class ActivityEvidenceChain(BaseModel):
    """Response envelope for GET /api/chronicler/episodes/{id}/evidence-chain."""

    episode_id: str
    layer: str
    """The episode's IEA layer. Only ``activity`` rows carry a meaningful chain;
    ``intent``/``evidence`` rows return whatever links they happen to have."""
    confidence: str
    """The activity's derived confidence (``high``/``medium``/``low``)."""
    evidence_refs: list[str] = Field(default_factory=list)
    """Denormalized point-event id list from ``episodes.evidence_refs``."""
    links: list[EvidenceChainLink] = Field(default_factory=list)
    """Resolved evidence links, ordered by point-event ``occurred_at`` ASC."""


# ── Low-confidence correction prompts (IEA, tasks.md S9a) ──────────────────


class CorrectionPrompt(BaseModel):
    """One low-confidence activity surfaced for owner confirmation / relabel.

    The write path reuses the existing corrections overlay: submit a correction
    via ``POST /api/chronicler/episodes/{id}/corrections`` (which records a
    non-destructive ``overrides`` row). Once an override exists the prompt drops
    off the list (its ``corrected_at`` becomes non-NULL).
    """

    episode_id: str
    source_name: str
    episode_type: str
    title: str | None = None
    start_at: datetime
    end_at: datetime | None = None
    best_guess_lane: str | None = None
    """The lane the activity is currently counted toward (``lane_for_activity``),
    or null when its source/type maps to no lane."""
    confidence: str
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    """Number of corroborating evidence links — low here is why confidence is low."""


class CorrectionPrompts(BaseModel):
    """Response envelope for GET /api/chronicler/correction-prompts."""

    start_at: datetime
    end_at: datetime
    tz: str
    prompts: list[CorrectionPrompt] = Field(default_factory=list)
    """Low-confidence activities, ordered by start_at ASC."""


# ── Daily rollups + flags (bu-333dq, telemetry-distillation bead 5) ────────


class RollupLaneRow(BaseModel):
    """One lane's totals for a single local day, from GET /api/chronicler/rollups.

    Mirrors ``chronicler.daily_rollups`` (one row per ``(local_date, lane)``),
    zero-filled for every lane in ``aggregations.LANES`` so a client never has
    to distinguish "no row" from "zero seconds" — the same convention
    ``rollups.compute_daily_lane_rollup`` uses server-side.
    """

    lane: str
    seconds: int = 0
    episode_count: int = 0
    distinct_place_count: int | None = None
    unavailable: bool = False
    """True when a source contributing to this lane is flagged ``feeder_dark``
    for this day (``daily_rollup_flags`` detail.dark_sources intersects
    ``aggregations.sources_for_lane(lane)``). A client MUST render this as
    "data unavailable" for the lane, never as a truthful zero — the day's
    ``seconds``/``episode_count`` for this lane may legitimately be 0 because
    the feeder producing them was down, not because nothing happened."""


class RollupFlagRow(BaseModel):
    """One deterministic anomaly-flag row from ``chronicler.daily_rollup_flags``."""

    flag_type: str
    """One of ``feeder_dark``, ``sleep_missing``, ``routine_break``,
    ``lane_share_outlier`` (design doc §3.4)."""
    severity: str
    """One of ``info``, ``warning``."""
    detail: dict[str, Any] = Field(default_factory=dict)


class RollupDay(BaseModel):
    """One local calendar day's rollup + flags, from GET /api/chronicler/rollups."""

    local_date: str
    """ISO-8601 date string (YYYY-MM-DD), local to ``timezone``."""
    timezone: str
    status: str
    """One of:

    - ``materialized`` — the ``chronicler_rollup_daily`` job has written this
      day's ``daily_rollups`` rows; ``lanes``/``flags`` reflect real data.
    - ``not_yet_materialized`` — no rows exist yet, because the day has not
      fully elapsed or the job's lookback window has not reached it. A
      legitimate absence, not a degraded/error state: never render this as a
      false all-clear zero, but also never set
      ``RollupsResponse.rollups_source_error`` for it.
    - ``unknown`` — the query for this window failed (see
      ``RollupsResponse.rollups_source_error``); this day's ``lanes``/
      ``flags`` are empty because nothing could be read, not because nothing
      happened or nothing was materialized yet."""
    lanes: list[RollupLaneRow] = Field(default_factory=list)
    """Empty when ``status='not_yet_materialized'``; one entry per
    ``aggregations.LANES`` (zero-filled) when ``status='materialized'``."""
    flags: list[RollupFlagRow] = Field(default_factory=list)


class RollupsResponse(BaseModel):
    """Response envelope for GET /api/chronicler/rollups.

    Degraded-envelope convention (butlers/CLAUDE.md "API Conventions"): a
    genuine query failure (e.g. the database connection drops mid-request)
    sets ``rollups_source_error=True`` and every requested day is returned
    with ``status='unknown'`` and empty ``lanes``/``flags`` — never a
    truthful empty/zero result silently mistaken for "nothing happened."
    This is a distinct failure mode from ``RollupDay.status=
    'not_yet_materialized'`` (a legitimately absent day — never sets this
    flag) and from a lane's ``unavailable=True`` (a known feeder outage on an
    otherwise-successfully-read day) — three distinct "no number here"
    states, three distinct signals, per the classify-before-flagging
    principle.
    """

    start_date: str
    end_date: str
    tz: str = "Asia/Singapore"
    days: list[RollupDay] = Field(default_factory=list)
    """Ordered by local_date ASC, one entry per day in [start_date, end_date]."""
    rollups_source_error: bool = False
    """True when the underlying query raised instead of returning rows. See
    class docstring — never treat a missing/false value as a hard guarantee
    of freshness, only as "this request did not fail outright"."""


# ── Daily balance vs usual (IEA, tasks.md S9b, bu-jc6htw.2) ─────────────────


class BalanceLaneRow(BaseModel):
    """One lane's balance for the target day, from GET /api/chronicler/balance.

    Baseline is a trailing rolling-window mean over ``chronicler.daily_rollups``
    (see ``balance.compute_daily_balance``) — the same materialized per-day
    totals ``GET /api/chronicler/rollups`` reads, so this surface can never
    diverge from the rollup numbers a client already renders elsewhere.
    """

    lane: str
    seconds: int = 0
    baseline_seconds: float | None = None
    """Trailing rolling-window mean seconds for this lane, or null when there
    is no materialized rollup history yet within the lookback window — not
    the same as a real 0 baseline."""
    delta_seconds: float | None = None
    """``seconds - baseline_seconds``. Null whenever ``baseline_seconds`` is
    null."""
    baseline_sample_days: int = 0
    unavailable: bool = False
    """True when a source contributing to this lane is ``feeder_dark`` for
    the target day (mirrors ``RollupLaneRow.unavailable``) — render this as
    'data unavailable', never as a truthful zero/delta."""


class BalanceResponse(BaseModel):
    """Response envelope for GET /api/chronicler/balance.

    Degraded-envelope convention (butlers/CLAUDE.md "API Conventions"): a
    genuine query failure sets ``balance_source_error=True`` and ``lanes``
    is returned empty — never a truthful empty/zero result silently mistaken
    for "nothing happened". This is distinct from ``status=
    'not_yet_materialized'`` (a legitimately absent day — never sets this
    flag) and from a lane's ``unavailable=True`` (a known feeder outage on an
    otherwise-successfully-read day).
    """

    local_date: str
    timezone: str
    status: str
    """One of ``materialized`` / ``not_yet_materialized`` / ``unknown`` —
    same three-state contract as ``RollupDay.status``."""
    baseline_lookback_days: int
    lanes: list[BalanceLaneRow] = Field(default_factory=list)
    """Empty when ``status != 'materialized'``; one entry per
    ``aggregations.LANES`` (zero-filled) otherwise."""
    balance_source_error: bool = False


# ── Trends (IEA, tasks.md S9b, bu-jc6htw.2) ─────────────────────────────────


class TrendLaneDay(BaseModel):
    """One lane's balance for one day within a trends window."""

    local_date: str
    status: str
    """One of ``materialized`` / ``not_yet_materialized`` / ``unknown``."""
    seconds: int = 0
    baseline_seconds: float | None = None
    delta_seconds: float | None = None
    unavailable: bool = False
    """See ``BalanceLaneRow.unavailable`` — same feeder_dark cross-reference,
    evaluated per day."""


class TrendLaneSeries(BaseModel):
    """One lane's day-by-day series across the requested trends window."""

    lane: str
    days: list[TrendLaneDay] = Field(default_factory=list)
    """Ordered by local_date ASC, one entry per day in [start_date, end_date]."""
    streak_days: int = 0
    """Trailing run of consecutive non-zero-activity days ending at
    ``end_date`` (``balance.compute_lane_streak``). 0 when the most recent
    day has no activity in this lane."""


class TrendAnomaly(BaseModel):
    """One day where a lane's total deviated sharply from its baseline.

    Flagged by ``balance.is_lane_anomalous`` — requires both a minimum
    baseline sample size and a delta clearing an absolute-and-relative
    threshold, so early-history noise never produces a fabricated anomaly.
    """

    lane: str
    local_date: str
    seconds: int
    baseline_seconds: float
    delta_seconds: float
    direction: str
    """``"spike"`` when seconds > baseline, ``"drop"`` when seconds < baseline."""


class TrendsResponse(BaseModel):
    """Response envelope for GET /api/chronicler/trends.

    Degraded-envelope convention: ``trends_source_error=True`` means the
    underlying rollup query raised — ``lanes``/``anomalies`` are returned
    empty in that case, never a truthful empty/zero result.
    """

    window: str
    """``"week"`` or ``"month"``."""
    start_date: str
    end_date: str
    tz: str = "Asia/Singapore"
    baseline_lookback_days: int
    lanes: list[TrendLaneSeries] = Field(default_factory=list)
    anomalies: list[TrendAnomaly] = Field(default_factory=list)
    """Ordered by local_date ASC, then lane ASC."""
    trends_source_error: bool = False


# ── Who-you-were-with (IEA, tasks.md S9b, bu-jc6htw.2) ──────────────────────


class CompanionEntry(BaseModel):
    """One resolved (or unattributed) companion for a who-you-were-with window.

    Identity resolution happens once, at write time, in
    ``adapters.comms.CommsSocialAdapter`` (message sender -> entity via
    ``relationship.entity_facts``, RFC D8 evidence-surface grant
    ``core_150``) — never re-resolved here. Per RFC 0014 §D17, the chronicler
    dashboard API itself must not read cross-schema; ``entity_id`` here is
    chronicler's own already-resolved ``episode_entities.entity_id``, and
    ``display_name`` is filled in via ``DatabaseManager.fan_out_with_status``
    against the relationship butler's OWN pool (the same sanctioned
    cross-BUTLER pattern ``GET /ops/sessions`` uses) — not a cross-schema
    join through the chronicler pool.
    """

    entity_id: str | None = None
    """Null when the companion could not be resolved to an entity
    (``unattributed=True``)."""
    display_name: str | None = None
    """Null when unattributed, OR when entity_id is known but the
    relationship-butler name lookup failed/returned nothing — see
    ``WhoYouWereWithResponse.companion_names_unavailable`` to distinguish the
    two."""
    unattributed: bool = False
    channel: str
    """E.g. ``"Telegram"``, ``"email"``, ``"WhatsApp"``, ``"Discord"``, or
    ``"in-person"`` for a non-comms (e.g. co-presence) social activity."""
    co_present_seconds: float
    episode_count: int


class WhoYouWereWithResponse(BaseModel):
    """Response envelope for GET /api/chronicler/who-you-were-with."""

    start_at: datetime
    end_at: datetime
    tz: str
    companions: list[CompanionEntry] = Field(default_factory=list)
    """Sorted by co_present_seconds DESC."""
    companion_names_unavailable: bool = False
    """True when entity_id -> display_name resolution (the relationship
    butler fan_out lookup) failed. Companion identity/duration/channel data
    is still chronicler's own and remains trustworthy; only display names are
    degraded. Distinct from an entry's own ``unattributed`` (identity
    genuinely unknown, not a lookup failure)."""
    who_you_were_with_source_error: bool = False
    """True when the chronicler-own-schema episode query itself failed —
    ``companions`` is empty in that case, never a truthful empty result."""


class RoutineRow(BaseModel):
    """A row from GET /api/chronicler/routines (bu-whhll.9)."""

    id: str
    dow_mask: int
    """Bitmask over ISO weekday, bit 0 = Monday ... bit 6 = Sunday."""
    window_start_local: time
    window_end_local: time
    timezone: str
    label: str
    support_count: int
    confidence: float
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    origin: str
    """``mined`` (weekly job) or ``declared`` (owner bootstrap, bu-whhll.11)."""
    enabled: bool
    created_at: datetime
    updated_at: datetime


class CreateRoutineRequest(BaseModel):
    """Request body for POST /api/chronicler/routines (bu-whhll.11).

    Owner-declared work-schedule bootstrap — "I work Mon-Fri 09:30-19:30 at
    <label>". Written straight into ``chronicler.routines`` with
    ``origin='declared'`` so the occupation-inference adapter picks it up on
    its next run (inference works immediately, without waiting for the weekly
    miner to accrue observed support).

    ``dow_mask`` is a bitmask over ISO weekday, bit 0 = Monday ... bit 6 =
    Sunday (``1 << date.weekday()``); at least one day must be set. The window
    is a same-day local wall-clock range (``window_end_local`` strictly after
    ``window_start_local`` — midnight-spanning windows are out of scope for
    v1, matching the table CHECK).
    """

    dow_mask: int = Field(..., ge=1, le=127)
    window_start_local: time
    window_end_local: time
    label: str = Field(..., min_length=1)
    timezone: str = "Asia/Singapore"
    enabled: bool = True


class UpdateRoutineRequest(BaseModel):
    """Request body for PATCH /api/chronicler/routines/{id}.

    ``enabled``/``label`` are editable on any routine (the owner-review
    surface). The schedule fields (``dow_mask``, ``window_start_local``,
    ``window_end_local``, ``timezone``) are only editable on owner-declared
    routines; passing any of them for a mined routine is a 400 — the weekly
    miner owns a mined routine's window and refreshes it on its next run.
    ``support_count``/``confidence``/``evidence_summary`` are never editable.
    All fields are optional and independently settable.
    """

    enabled: bool | None = None
    label: str | None = Field(default=None, min_length=1)
    dow_mask: int | None = Field(default=None, ge=1, le=127)
    window_start_local: time | None = None
    window_end_local: time | None = None
    timezone: str | None = None


__all__ = [
    "ActivityEvidenceChain",
    "AggregateByDayRow",
    "BalanceLaneRow",
    "BalanceResponse",
    "CategoryBucket",
    "CategoryBuckets",
    "CompanionEntry",
    "CorrectionPrompt",
    "CorrectionPrompts",
    "CreateRoutineRequest",
    "EvidenceChainLink",
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
    "RollupDay",
    "RollupFlagRow",
    "RollupLaneRow",
    "RollupsResponse",
    "RoutineRow",
    "SourceBreakdownEntry",
    "SourceStateRow",
    "SubsourceCheckpoint",
    "SubmitCorrectionRequest",
    "TrendAnomaly",
    "TrendLaneDay",
    "TrendLaneSeries",
    "TrendsResponse",
    "UpdateRoutineRequest",
    "WhoYouWereWithResponse",
]
