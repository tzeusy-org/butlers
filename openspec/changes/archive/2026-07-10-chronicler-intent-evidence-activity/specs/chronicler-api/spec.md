# Chronicler API — Spec delta for chronicler-intent-evidence-activity

## MODIFIED Requirements

### Requirement: Chronicler Aggregations

The API SHALL expose Chronicler-owned read endpoints under
`/api/chronicler/aggregate/*` that return time-bucketed summaries of
corrected episode rows, suitable for direct consumption by dashboard
visualizations without further client-side computation.

The initial endpoint set SHALL include:

- `GET /api/chronicler/aggregate/by-category`
- `GET /api/chronicler/aggregate/by-day`

All aggregate endpoints SHALL read exclusively from `chronicler.v_episodes_corrected` (and, where relevant, `chronicler.v_point_events_corrected`). Their SQL relation references — bare or schema-qualified — SHALL resolve only to relations within the `chronicler` schema. No aggregate handler SHALL invoke an LLM under any code path.

`GET /api/chronicler/aggregate/by-category` SHALL count the `activity` layer only
and roll up into the Activity lane taxonomy (`sleep`, `exercise`, `work`,
`play`, `social`, `travel`, `eat`, `rest`). `intent` and `evidence` layers are
excluded. No "calendar" lane is returned; calendar time appears only via the
activity lane it corroborates. `by-day` follows the same counting rule.

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

#### Scenario: By-category excludes intent and evidence layers

- **WHEN** a window contains a 5-hour uncorroborated calendar block plus inferred
  activities
- **THEN** the calendar block contributes 0 seconds to every lane
- **AND** the returned buckets use Activity lane names only

#### Scenario: Lane buckets carry confidence breakdown

- **WHEN** by-category is requested
- **THEN** each lane bucket reports how much of its time is low-confidence
- **AND** lanes are returned sorted by total time

#### Scenario: Unmapped active source is dropped with a warning

- **WHEN** the aggregate handler encounters an `activity`-layer episode
  whose `(source_name, episode_type)` pair resolves to no Activity lane
  (an unmapped source)
- **THEN** the episode SHALL NOT contribute to any lane bucket (the lane
  taxonomy has no `other` lane, so unmapped activity is dropped rather
  than surfaced as an `other` bucket)
- **AND** the handler SHALL emit a warning log and set the OTel span
  attribute `chronicler.aggregate.unmapped_source = <source_name>` so
  operators can detect taxonomy drift

## ADDED Requirements

### Requirement: Daily Balance Endpoint

A read endpoint SHALL return the day's per-lane balance annotated against the
owner's rolling baseline ("vs usual").

#### Scenario: Balance returns deltas vs usual

- **WHEN** a client requests the daily balance for a date
- **THEN** each lane returns the day's total and a signed delta vs the owner's
  rolling baseline
- **AND** a lane with no activity returns zero with its baseline for context

### Requirement: Trends Endpoint

A read endpoint SHALL return week- and month-grained balance trends, streaks, and
anomalies derived from the chronicler's own synthesized baselines.

#### Scenario: Week trends return per-lane series

- **WHEN** a client requests trends for a week window
- **THEN** a per-lane time series is returned
- **AND** notable streaks/anomalies (e.g. consecutive work days) are reported

### Requirement: Who-You-Were-With Endpoint

A read endpoint SHALL return the resolved people the owner spent time with in a
window, with co-present time and channel, resolving identity via
`relationship.entity_facts`.

#### Scenario: Returns resolved companions for a day

- **WHEN** a client requests who-you-were-with for a date
- **THEN** each entry names a resolved entity, the co-present duration, and the
  channel (in-person vs a comms channel)
- **AND** unresolved participants are returned as unattributed rather than
  dropped

### Requirement: Activity Evidence Chain Endpoint

A read endpoint SHALL return the evidence chain for an activity — each
corroborating signal with its source — so a client can answer "why?".

#### Scenario: Evidence chain returned for an activity

- **WHEN** a client requests the evidence chain for an activity id
- **THEN** the response lists each `evidence_ref` with its source name and a
  human-readable descriptor
- **AND** the activity's confidence is included

### Requirement: Low-Confidence Correction Prompts

A read endpoint SHALL return the day's low-confidence activities as correction
prompts the owner can confirm or relabel, reusing the existing corrections
overlay for writes.

#### Scenario: Low-confidence blocks surfaced as prompts

- **WHEN** a client requests correction prompts for a date
- **THEN** low-confidence activities are returned with their best-guess lane and
  evidence
- **AND** confirming or relabeling writes a non-destructive correction overlay

## Source References

- `chronicler-api/spec.md` §5.5 (Chronicler Aggregations), §5.3 (Chronicler
  Corrections), §5.8 (Episode Participant Resolution Read Path).
- `butler-chronicler/spec.md` §4.15 (Calendar Scheduled Blocks Are Not
  Attendance Assertions).
- RFC 0014 (Chronicler Time Butler).
