# Chronicler API — Delta

This delta extends the existing `chronicler-api` capability with two new
read-only Requirements: aggregate endpoints and source-state visibility.
All existing requirements (Temporal Reads, Response Provenance,
Corrections, Operational Timeline Route Preserved) remain unchanged.

## ADDED Requirements

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

#### Scenario: Provenance-ref episode outside cached window

- **WHEN** a client requests the day-close endpoint for a `(date, tz)`
  where a cache entry exists AND an episode that was cited in
  `provenance_refs` when the cache was built has `updated_at >
  cache_built_at` (regardless of whether its current time range still
  overlaps the cached window)
- **THEN** the API SHOULD treat the cache as stale, respond with
  `{stale: true, cache_built_at, last_invalidating_event_at}`, and SHALL
  NOT return the cached prose
- **AND** `last_invalidating_event_at` SHOULD be set to the episode's
  `updated_at`

> **Rationale (signal 6):** A cited episode may have been corrected to
> move its canonical time range outside the cached window after the cache
> was built. The window-scoped staleness check (signals 1–2) will miss
> such a row because it is no longer within the window. Joining against
> the stored `provenance_refs` set catches this drift. SHOULD (not SHALL)
> because this is a best-effort signal: the implementation must have stored
> `provenance_refs` for it to apply, and a cache entry with an empty
> `provenance_refs` list cannot trigger this path.

#### Scenario: Provenance-ref point event outside cached window

- **WHEN** a client requests the day-close endpoint for a `(date, tz)`
  where a cache entry exists AND a point event that was cited in
  `provenance_refs` when the cache was built has `updated_at >
  cache_built_at` (regardless of whether its current `canonical_occurred_at`
  still falls within the cached window)
- **THEN** the API SHOULD treat the cache as stale, respond with
  `{stale: true, cache_built_at, last_invalidating_event_at}`, and SHALL
  NOT return the cached prose
- **AND** `last_invalidating_event_at` SHOULD be set to the point event's
  `updated_at`

> **Rationale (signal 7):** Parallel to signal 6 but for cited point
> events. A point event's `canonical_occurred_at` may have been corrected
> to fall outside the cached window, making the window-scoped signals 3–4
> blind to it. Joining against `provenance_refs` closes this gap. Same
> SHOULD framing applies: conditioned on `provenance_refs` being populated
> in the cache entry.

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
