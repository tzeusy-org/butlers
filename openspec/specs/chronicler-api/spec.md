# Chronicler API

## Purpose

Defines the backend API contract for Chronicler-owned retrospective time reads
and owner corrections. This capability intentionally does not define dashboard
UX or claim the existing operational `/timeline` route.

## Requirements

### Requirement: Chronicler Temporal Reads

The API SHALL expose Chronicler-owned read endpoints under `/api/chronicler/*`
for retrospective event and episode queries.

The initial endpoint set SHALL include:

- `GET /api/chronicler/events`
- `GET /api/chronicler/episodes`
- `GET /api/chronicler/episodes/{episode_id}`
- `GET /api/chronicler/episodes/{episode_id}/events`
- `GET /api/chronicler/episodes/{episode_id}/corrections`

#### Scenario: Events queried by time range

- **WHEN** a client requests Chronicler point events for a time range
- **THEN** the API SHALL read from Chronicler-owned derived event tables
- **AND** it SHALL support filtering by source, event type, confidence where
  applicable, privacy tier, source reference status, and tombstone state
- **AND** it SHALL accept `start_at`, `end_at`, `source`, `event_type`,
  `privacy_tier`, `source_ref_status`, `include_tombstoned`, `limit`, and
  `cursor` query parameters
- **AND** it SHALL return stable pagination metadata with `items`,
  `next_cursor`, and `has_more`

#### Scenario: Episodes queried by time range

- **WHEN** a client requests Chronicler episodes for a time range
- **THEN** the API SHALL include episodes whose intervals overlap the requested
  range, including open episodes where `ended_at = NULL`
- **AND** it SHALL support filtering by source, episode type, confidence,
  privacy tier, source reference status, and tombstone state
- **AND** it SHALL preserve overlapping episodes rather than collapsing them
  into one primary activity
- **AND** it SHALL accept `start_at`, `end_at`, `source`, `episode_type`,
  `min_confidence`, `privacy_tier`, `source_ref_status`, `include_open`,
  `include_tombstoned`, `limit`, and `cursor` query parameters

#### Scenario: Episodes filtered by participant entity

- **WHEN** a client requests `/api/chronicler/episodes?participant_entity_id=<uuid>`
- **THEN** the API SHALL return episodes that have at least one row in
  `chronicler.episode_entities` referencing the supplied `entity_id`,
  regardless of the role (`'owner'`, `'organizer'`, or `'participant'`)
- **AND** the response SHALL preserve the existing pagination shape
  (`items`, `next_cursor`, `has_more`)
- **AND** the API SHALL also continue to accept the existing
  `entity_id=<uuid>` parameter, which SHALL be interpreted as an
  owner-only filter against the derived `chronicler.episodes.entity_id`
  column during the transition window
- **AND** the API SHALL NOT require both parameters; passing both SHALL
  evaluate the participant-join filter and apply the owner-column
  filter as a conjunctive constraint

#### Scenario: Episode detail queried

- **WHEN** a client requests `GET /api/chronicler/episodes/{episode_id}`
- **THEN** the API SHALL return the corrected current episode view plus original
  derived identifiers, active override status, and source provenance
- **AND** it SHALL return `404` when the episode is not visible or does not
  exist

#### Scenario: Interactive reads stay inside Chronicler

- **WHEN** the API answers a Chronicler read request
- **THEN** it SHALL read Chronicler-owned derived events, episodes, links, and
  overrides
- **AND** it SHALL NOT issue arbitrary direct cross-schema reads against source
  butler tables

### Requirement: Chronicler Response Provenance

Chronicler API responses SHALL expose enough provenance for the client to
understand source evidence, uncertainty, privacy, and correction state.

#### Scenario: Episode response includes boundary semantics

- **WHEN** an episode is returned
- **THEN** the response SHALL include start and end boundary provenance,
  confidence, source references, source reference status, privacy tier,
  precision policy, retention policy, tombstone state, and active override
  status where present

#### Scenario: Episode response includes participant entity set

- **WHEN** an episode is returned by any Chronicler read endpoint
  (`GET /api/chronicler/episodes`,
  `GET /api/chronicler/episodes/{id}`, the entity-activity aggregator,
  or any future endpoint that surfaces episode rows)
- **THEN** the episode object SHALL include a `participant_entity_ids`
  field of type `array<uuid>`
- **AND** the array SHALL be the aggregated set of `entity_id` values
  from `chronicler.episode_entities` for the episode, sorted by
  role precedence (`'owner'` first, then `'organizer'`, then
  `'participant'`) with `entity_id ASC` as the deterministic tiebreak
  within a role bucket
- **AND** the array SHALL be empty (`[]`, never null) when no
  participants are linked
- **AND** the existing `entity_id` field SHALL continue to be returned
  during the transition window, equal to the owner participant when
  present, else `null`
- **AND** the array SHALL NOT include unresolved attendees (attendees
  for whom the upstream calendar module did not resolve a
  `public.entities` row)

#### Scenario: Event response includes source semantics

- **WHEN** an event is returned
- **THEN** the response SHALL include occurred-at timestamp, source channel,
  source provider, event type, source reference, privacy tier, precision policy,
  retention policy, source reference status, and tombstone state

#### Scenario: Episode-event links are available

- **WHEN** a client requests supporting evidence for an episode
- **THEN** the API SHALL expose linked point events with relation semantics such
  as `starts`, `ends`, `supports`, `occurs_during`, or `contradicts`

### Requirement: Chronicler Corrections

The API SHALL support owner corrections by creating override or superseding
records without mutating source evidence.

The initial correction endpoint set SHALL include:

- `POST /api/chronicler/episodes/{episode_id}/corrections`

#### Scenario: Episode correction submitted

- **WHEN** the owner submits a correction for an episode type, title, start time,
  end time, or explanatory note
- **THEN** the API SHALL create an override or superseding derived record
- **AND** it SHALL retain the original source evidence and original derived
  record for audit
- **AND** corrected read views SHALL prefer the active correction
- **AND** the request body SHALL allow only correction fields owned by
  Chronicler: corrected type, title, start time, end time, note, and active
  status

#### Scenario: Correction policy inherited

- **WHEN** a correction is created for an episode or event
- **THEN** the correction record SHALL inherit the stricter effective privacy,
  precision, and retention policy from the target record and source contract

#### Scenario: Correction history returned

- **WHEN** a client requests correction history for an episode
- **THEN** the API SHALL return the ordered correction audit trail without
  exposing source fields that have been tombstoned or precision-reduced

#### Scenario: Invalid correction rejected

- **WHEN** a correction request has invalid timestamps, attempts to change source
  evidence, or violates privacy/retention policy
- **THEN** the API SHALL reject it with a structured `400` response
- **AND** it SHALL NOT create a partial override record

### Requirement: Operational Timeline Route Preserved

The Chronicler API change SHALL NOT repurpose the existing operational
`/timeline` dashboard/API route.

#### Scenario: Chronicler API namespace used

- **WHEN** Chronicler read or correction endpoints are added
- **THEN** they SHALL live under `/api/chronicler/*`
- **AND** any future user-facing timeline route SHALL require a separate
  dashboard/API spec amendment before replacing or reusing the operational
  `/timeline` concept

### Requirement: Chronicler Aggregations

The API SHALL expose Chronicler-owned read endpoints under
`/api/chronicler/aggregate/*` that return time-bucketed summaries of
corrected episode rows, suitable for direct consumption by dashboard
visualizations without further client-side computation.

The initial endpoint set SHALL include:

- `GET /api/chronicler/aggregate/by-category`
- `GET /api/chronicler/aggregate/by-day`

All aggregate endpoints SHALL read exclusively from `chronicler.v_episodes_corrected` (and, where relevant, `chronicler.v_point_events_corrected`). Their SQL relation references — bare or schema-qualified — SHALL resolve only to relations within the `chronicler` schema. No aggregate handler SHALL invoke an LLM under any code path.

#### Scenario: Corrected-view-only reads

- **WHEN** a client requests `/api/chronicler/aggregate/by-category` or
  `/api/chronicler/aggregate/by-day`
- **THEN** the handler SHALL execute SQL whose every table or view
  reference resolves to a relation in the `chronicler` schema, regardless
  of whether the reference is bare (relying on `search_path`) or
  schema-qualified
- **AND** the corrected-view set (`chronicler.v_episodes_corrected`,
  `chronicler.v_point_events_corrected`) SHALL be the only views
  accessed
- **AND** a guardrail test SHALL parse handler source files, extract
  SQL string literals, and fail the build when any extracted relation
  name does not appear in the `chronicler` schema's list of known
  relations

#### Scenario: Provenance carry-forward on bucket records

- **WHEN** an aggregate response is returned
- **THEN** each bucket record SHALL include a `source_breakdown` array
  enumerating the contributing `source_name` values with their
  per-source `total_seconds` and `episode_count`
- **AND** each bucket record SHALL include a `precision` field equal to
  the **least-precise** value across contributing rows (ordering: `exact
  > minute > hour > day > unknown`) so downstream consumers cannot infer
  a tighter precision than the underlying evidence supports
- **AND** each bucket record SHALL include a `retention_floor_days` field
  equal to the **shortest** non-NULL `retention_days` across contributing
  rows (or NULL if all rows inherit the Chronicler default), so retention
  obligations carry through the projection
- **AND** the response SHALL preserve the corrected-view-only and
  privacy-filter rules so that a downstream consumer cannot recover
  a duration that excludes tombstoned or restricted rows from the
  bucket totals

#### Scenario: Privacy tier filtering with safe defaults

- **WHEN** an aggregate request omits the `privacy_tier` filter
- **THEN** the handler SHALL exclude episodes with `privacy_tier =
  restricted` from all bucket sums
- **AND** episodes with `privacy_tier = sensitive` SHALL contribute
  to bucket durations and counts BUT their titles, payloads, and any
  identifying source-ref details SHALL NOT appear in the response
- **AND** `privacy_tier` MAY be supplied as a comma-delimited list to
  narrow the set further

#### Scenario: Tombstone exclusion default

- **WHEN** an aggregate request omits `include_tombstoned`
- **THEN** the handler SHALL exclude all rows with `tombstone_at IS NOT
  NULL AND tombstone_at <= now()`
- **AND** when `include_tombstoned=true` is supplied, tombstoned rows
  SHALL be included AND each contributing record SHALL be flagged
  `tombstoned: true` in its source-breakdown entry

#### Scenario: No LLM invocation

- **WHEN** any aggregate handler executes
- **THEN** no `anthropic`, `openai`, `claude_agent_sdk`, or
  `butlers.chronicler.interpretation` import or invocation SHALL occur
- **AND** a guardrail test SHALL scan handler files and fail any change
  that introduces such an import

#### Scenario: Deterministic ordering and stable pagination

- **WHEN** an aggregate response contains multiple bucket records
- **THEN** the records SHALL be returned in a deterministic order:
  `by-category` SHALL sort by `total_seconds DESC` then `category ASC`;
  `by-day` SHALL sort by `(day ASC, category ASC)`
- **AND** the response SHALL include the same pagination shape
  expectations as existing list endpoints (no partial bucket emission;
  cursor pagination NOT required for the initial single-page
  responses, but if added later SHALL match the existing
  `next_cursor` / `has_more` shape from `Chronicler Temporal Reads`)

#### Scenario: Timezone-aware day buckets

- **WHEN** a `by-day` request supplies an IANA `tz` parameter
- **THEN** day boundaries SHALL be computed in that timezone
- **AND** when `tz` is omitted, day boundaries SHALL default to `UTC`
- **AND** DST-extended (25-hour) and DST-shortened (23-hour) calendar
  days SHALL be treated as a single bucket each, with the bucket's
  `total_seconds` reflecting the actual duration overlap rather than
  a normalized 24-hour window
- **AND** the response SHALL include each day's start and end timestamps
  in the requested `tz` so the consumer can verify the bucket boundary
  without re-deriving DST rules

#### Scenario: Invalid time range rejected

- **WHEN** an aggregate request is missing `start_at` or `end_at`,
  supplies `end_at <= start_at`, or supplies an unrecognized `tz`
- **THEN** the API SHALL reject it with a structured `400` response
  whose `code` is one of `invalid_time_range`, `invalid_timezone`,
  or `missing_parameter`
- **AND** it SHALL NOT return any partial bucket records
- **AND** the response shape SHALL match the existing
  `ErrorResponse` envelope (parallel to `Invalid correction
  rejected`)

#### Scenario: Unmapped active source surfaces as warning bucket

- **WHEN** the aggregate handler encounters episodes from an `active`
  source whose `(source_name, episode_type)` pair has no entry in the
  `category_for()` mapping
- **THEN** the contributing duration SHALL be summed into a bucket with
  `category = "other"`
- **AND** the handler SHALL emit a warning OTel span attribute
  `chronicler.aggregate.unmapped_source = <source_name>` so operators
  can detect taxonomy drift
- **AND** the bucket's `source_breakdown` SHALL still cite the unmapped
  `source_name` so the operator knows which adapter triggered the
  drift

### Requirement: Chronicler Source State Visibility

The API SHALL expose a read-only endpoint that lists the runtime state
of every registered source adapter, so that dashboard surfaces can
render disabled-lane affordances and operational diagnostics without
querying the database directly.

The endpoint SHALL be:

- `GET /api/chronicler/source-state`

#### Scenario: Source state listed with checkpoint diagnostics

- **WHEN** a client requests `GET /api/chronicler/source-state`
- **THEN** the API SHALL return one record per row in
  `chronicler.source_adapter_state`
- **AND** each record SHALL include `source_name`,
  `chronicler_compatibility`, `read_surface`, `boundary_semantics`,
  `optional_schema`, `active`, `inactive_reason`, and the **latest**
  `last_run_at` and `last_error` from `chronicler.projection_checkpoints`
  rows for that `source_name` across all `subsource` values (per-schema
  watermarks per migration `002_per_schema_watermarks.py`); when
  per-subsource detail is relevant, the record MAY include an optional
  `subsource_checkpoints` array enumerating each `(subsource,
  last_run_at, last_error)` tuple
- **AND** the records SHALL be returned in `source_name ASC` order

#### Scenario: Empty source-state on cold boot

- **WHEN** the daemon has just started and `source_adapter_state` has
  not yet been populated by the boot-time seeding
- **THEN** the endpoint SHALL respond `200 OK` with `data: []`
- **AND** it SHALL NOT respond `404` or any error code

#### Scenario: Optional-schema degradation surfaced

- **WHEN** a source has `optional_schema = true` AND its read surface
  is missing in this deployment
- **THEN** the record SHALL show `active = false` AND
  `inactive_reason` containing the specific missing schema or table
- **AND** the dashboard caller SHALL render the corresponding lane as
  disabled with the inactive reason as a tooltip

#### Scenario: Read-only contract

- **WHEN** the endpoint is invoked with any HTTP verb other than `GET`
- **THEN** the API SHALL respond `405 Method Not Allowed`
- **AND** no path on `/api/chronicler/source-state` SHALL accept
  client-supplied state mutations

### Requirement: Chronicler Day-Close Cache Surface

The API SHALL expose a read endpoint for cached `chronicler_day_close` Tier-2 prose AND a re-invocation endpoint that triggers the existing scheduled day-close path on demand. The read endpoint SHALL detect staleness against the canonical `chronicler.episodes`, `chronicler.point_events`, and `chronicler.overrides` rows in the cached window. The re-invocation endpoint SHALL be rate-limited and SHALL NOT introduce a new LLM call path beyond the one already declared in RFC 0014 §D5.

The endpoints SHALL be:

- `GET /api/chronicler/aggregate/day-close?date=YYYY-MM-DD&tz=...`
- `POST /api/chronicler/aggregate/day-close/refresh` (body: `{date, tz}`)

#### Scenario: Cache hit returns prose with provenance

- **WHEN** a client requests `GET /api/chronicler/aggregate/day-close`
  for a `(date, tz)` whose cache entry is fresh
- **THEN** the API SHALL return the cached `prose` text plus
  `provenance_refs` (the source-ref tuples cited by the prose) and
  `cache_built_at`
- **AND** no LLM SHALL be invoked

#### Scenario: Stale cache surfaces stale marker

- **WHEN** a client requests the day-close endpoint for a `(date, tz)`
  where any episode, point event, or override in the cached window has
  been tombstoned, updated, or created with a timestamp greater than the
  cache's `cache_built_at`
- **THEN** the API SHALL respond with `{stale: true, cache_built_at,
  last_invalidating_event_at}` and SHALL NOT return the cached prose
- **AND** `last_invalidating_event_at` SHALL be the maximum of the
  qualifying timestamps across all invalidators within the window
- **AND** no LLM SHALL be invoked

#### Scenario: Stale due to override creation

- **WHEN** the only invalidating change in the window is an override row
  whose `created_at > cache_built_at` (with the underlying episode's
  `updated_at` unchanged because precision-reduction or correction
  landed on the override row)
- **THEN** the cache SHALL still be reported stale
- **AND** the response SHALL set `last_invalidating_event_at` to the
  override's `created_at`

#### Scenario: User-clicked refresh re-invokes existing path

- **WHEN** a client POSTs to `/api/chronicler/aggregate/day-close/refresh`
  for a `(date, tz)`
- **THEN** the API SHALL re-invoke the existing scheduled
  `chronicler_day_close` Tier-2 entry point (RFC 0014 §D5) and, on
  success, write a new cache entry with a fresh `cache_built_at`
- **AND** the API SHALL NOT introduce a new LLM call path; the
  invocation MUST go through the same Tier-2 token-bound input path
  the cron-driven schedule uses

#### Scenario: Refresh rate limit enforced

- **WHEN** a client POSTs the refresh endpoint for a `(date, tz)` that
  has already been refreshed within the last 24 hours by any caller
- **THEN** the API SHALL respond `429 Too Many Requests` with `code:
  day_close_rate_limited`
- **AND** the response SHALL match the existing `ErrorResponse` envelope
  (`{ error: { code, message, butler, details } }`) with
  `retry_after_seconds` carried inside `details`
- **AND** no Tier-2 invocation SHALL occur


### Requirement: Episode Participant Resolution Read Path

The API SHALL expose participant entity membership as a first-class
provenance facet of every episode response. This is the read side of the
chronicler `episode_entities` storage shape introduced in
`butler-chronicler`.

#### Scenario: Aggregator endpoints surface participants

- **WHEN** any chronicler aggregator endpoint
  (`/api/chronicler/aggregate/by-category`,
  `/api/chronicler/aggregate/by-day`,
  `/api/chronicler/aggregate/day-close`) includes per-source breakdowns
  or per-episode citations
- **THEN** episode-level citation entries MAY include the
  `participant_entity_ids` array but SHALL NOT include resolved
  participant display names or any other personally identifying
  information beyond the UUID set
- **BECAUSE** display-name resolution is the relationship butler's
  responsibility; chronicler returns stable IDs only

#### Scenario: Privacy-tier filtering preserved

- **WHEN** an episode is filtered out by privacy semantics (the
  `restricted` server-side filter, or `sensitive` payload masking)
- **THEN** the `participant_entity_ids` array SHALL be omitted from the
  response together with the rest of the masked payload
- **AND** `restricted` episodes SHALL remain invisible regardless of
  the participant set; multi-entity tagging SHALL NOT create a side
  channel that reveals restricted episodes to participants

#### Scenario: Cursor-paginated participant filter is deterministic

- **WHEN** `/api/chronicler/episodes?participant_entity_id=<uuid>` is
  paginated via `cursor`
- **THEN** the underlying ORDER BY SHALL remain `start_at DESC, id DESC`
  (matching the existing list contract) so that adding the join does
  not destabilize pagination

### Requirement: Episode Tier-2 Explain Endpoint

The Chronicler SHALL expose `POST /api/chronicler/episodes/{episode_id}/explain`,
a rate-limited Tier-2 drilldown that assembles a token-bounded bundle (episode
detail in corrected view, linked point events, and correction history) and
dispatches it to an LLM for a single episode. The bundle SHALL be capped so the
episode detail is never truncated and only the supporting arrays shrink to fit
the character budget.

#### Scenario: Explain assembles a token-bounded bundle

- **WHEN** `POST /api/chronicler/episodes/{episode_id}/explain` is called for an
  existing, non-tombstoned, non-sensitive episode and a dispatch callable is wired
- **THEN** the endpoint SHALL build a bundle from the corrected episode, its
  linked events, and its correction history, bounded to the configured character
  cap, and dispatch it for Tier-2 interpretation

#### Scenario: Sensitive and restricted episodes are excluded

- **WHEN** the target episode has `canonical_privacy` of `sensitive` or `restricted`
- **THEN** the endpoint SHALL return HTTP 403 with `code=episode_explain_excluded`
  and SHALL NOT send any payload to the LLM

#### Scenario: Per-episode rate limit enforced

- **WHEN** an explain for the same episode was performed within the rate-limit
  window (24 hours)
- **THEN** the endpoint SHALL return HTTP 429 with
  `code=episode_explain_rate_limited` and `details.retry_after_seconds`

#### Scenario: Missing episode or unavailable dispatch

- **WHEN** the episode does not exist
- **THEN** the endpoint SHALL return HTTP 404
- **AND WHEN** no dispatch callable is wired in the deployment
- **THEN** the endpoint SHALL return HTTP 503 with `code=dispatch_unavailable`

### Requirement: Operational Sessions Escape Hatch

The Chronicler SHALL expose `GET /api/chronicler/ops/sessions`, a read-only
endpoint that returns exactly the sessions whose `trigger_source` matches the
projection exclusion set (`tick`, `qa`, `healing`, and the `schedule:*` prefix).
These rows are intentionally invisible to `/api/chronicler/episodes` and exist so
that engineers can audit scheduler cadence and background-job health without
direct database access.

#### Scenario: Ops sessions returned with filters

- **WHEN** `GET /api/chronicler/ops/sessions` is called with optional
  `trigger_source`, `since`, `until`, and `limit` (1-500, default 50) parameters
- **THEN** only sessions in the exclusion set SHALL be returned, ordered most
  recent first, each carrying `butler`, `session_id`, `trigger_source`,
  `started_at`, `completed_at`, `duration_ms`, `success`, and `model`

#### Scenario: Ops data never leaks into user-facing episodes

- **WHEN** a session is returned by `/api/chronicler/ops/sessions`
- **THEN** that same session SHALL NOT appear in `/api/chronicler/episodes`; the
  separation is enforced at the adapter layer, not by the endpoint

### Requirement: Projection Health Visibility

The Chronicler SHALL expose `GET /api/chronicler/projection-health`, a read-only
endpoint that returns one row per `projection_checkpoints` entry so that adapter
ingestion errors are surfaced without direct database access.

#### Scenario: Per-checkpoint health listed

- **WHEN** `GET /api/chronicler/projection-health` is called
- **THEN** the response SHALL include `source_name`, `subsource`, `last_error`,
  `last_run_at`, `rows_projected`, and `watermark` for every checkpoint, ordered
  by `source_name ASC, subsource ASC`
- **AND WHEN** the `projection_checkpoints` table is empty
- **THEN** the endpoint SHALL return an empty `data` array

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral)
- RFC 0006 (Database schema isolation)
- RFC 0007 (Dashboard and API Surface)
- RFC 0010 (Cross-Butler Briefing Exception)
- RFC 0014 (Chronicler Time Butler, Draft)
- RFC 0014 (Chronicler Time Butler) §D7 API Surface