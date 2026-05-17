## MODIFIED Requirements

### Requirement: Ingestion Event List (Paginated)
Return a unified stream of all ingestion events (ingested, filtered, errored) ordered by `received_at DESC` using **keyset (cursor) pagination**, with optional filtering. Offset+total pagination is removed: the `total` field SHALL NOT be returned in steady-state responses. If a count is unavoidable for a specific consumer, it MUST come from a ≥30-second TTL-cached approximate count (per CF-R2-2) and be clearly labeled as approximate; per-request `COUNT(*)` over a `UNION ALL` of `public.ingestion_events` and `connectors.filtered_events` at poll cadence is prohibited.

The query interface SHALL accept an opaque `cursor` token and a `limit`. The cursor encodes the `(received_at, id)` tuple of the last row returned and is used to seek the next page via an indexed `(received_at, id) < (cursor.received_at, cursor.id)` predicate (descending order). Responses include `items`, `next_cursor` (string or null when the page is the last), and MAY include `approximate_total` only when a TTL-cached count is available.

This is a **BREAKING** change for `useIngestionEvents` and any consumer of the previous offset/limit/total shape. The hook contract changes in lockstep: callers receive `{ items, nextCursor, isLoading, error }` instead of `{ items, total, page, ... }`.

#### Scenario: Keyset paginated list
- **WHEN** `ingestion_events_list(pool, limit=20, cursor=None)` is called
- **THEN** up to 20 rows are returned, most recent first (`received_at DESC, id DESC`)
- **AND** the result SHALL include events from both `public.ingestion_events` and `connectors.filtered_events` merged by `received_at DESC`
- **AND** the response SHALL include `next_cursor` encoding the `(received_at, id)` of the last returned row, or `null` if fewer than `limit` rows were returned

#### Scenario: Subsequent page via cursor
- **WHEN** `ingestion_events_list(pool, limit=20, cursor=<token>)` is called with the `next_cursor` from a previous response
- **THEN** only rows strictly older than the cursor position (`(received_at, id) < (cursor.received_at, cursor.id)`) are returned
- **AND** no `OFFSET` is used in the underlying SQL

#### Scenario: No COUNT at poll cadence
- **WHEN** the list endpoint is polled at the dashboard's default cadence (≥30s)
- **THEN** the response SHALL NOT execute a `COUNT(*)` over the unified `UNION ALL` per request
- **AND** if `approximate_total` is returned, it MUST originate from a TTL cache with TTL ≥ 30 seconds and be labeled approximate in the response schema

#### Scenario: Filtered by source channel
- **WHEN** `ingestion_events_list(pool, source_channel="email")` is called
- **THEN** only events with `source_channel = 'email'` are returned from both tables, ordered by keyset

#### Scenario: Response includes status field
- **WHEN** the unified list is returned
- **THEN** each row SHALL include a `status` field: `ingested` for rows from `public.ingestion_events`, or the `status` column value for rows from `connectors.filtered_events` (`filtered`, `error`, `replay_pending`, `replay_complete`, `replay_failed`)

#### Scenario: Response includes filter_reason field
- **WHEN** the unified list is returned
- **THEN** each row SHALL include a `filter_reason` field: `null` for ingested events, or the `filter_reason` column value for filtered/errored events

#### Scenario: Filtered by status
- **WHEN** `ingestion_events_list(pool, status="filtered")` is called
- **THEN** only events with the matching status are returned
- **AND** `status="ingested"` queries only `public.ingestion_events`
- **AND** all other status values query only `connectors.filtered_events`

#### Scenario: BREAKING hook contract
- **WHEN** a frontend consumer imports `useIngestionEvents`
- **THEN** the hook SHALL expose `{ items, nextCursor, isLoading, error, fetchNextPage }` and SHALL NOT expose `total` or `page`
- **AND** consumers depending on the previous offset/total shape MUST be migrated in the same change set

## ADDED Requirements

### Requirement: Pipeline Stats Endpoint
The system SHALL expose `GET /api/ingestion/pipeline?window=24h` returning aggregate pipeline counters (ingested, filtered, errored, routed, blocked) over the requested window. The endpoint MUST use a TTL cache or a materialized view; per-request `UNION ALL` aggregation across `public.ingestion_events` and `connectors.filtered_events` at poll cadence is prohibited. Aggregation strategy detail is delegated to the `connector-state-aggregates` spec.

#### Scenario: 24h window returns aggregates
- **WHEN** `GET /api/ingestion/pipeline?window=24h` is called
- **THEN** the response SHALL include counts for `ingested`, `filtered`, `errored`, `routed_by_butler` breakdown, and a `window` echo
- **AND** the values SHALL be served from a TTL cache (per `connector-state-aggregates`) or a materialized view — not a per-request `UNION ALL COUNT(*)`

#### Scenario: Degraded mode when aggregates unavailable
- **WHEN** the underlying aggregation source (Prometheus, materialized view) is unreachable
- **THEN** the endpoint SHALL return zeros for each counter together with `aggregates_available: false`
- **AND** the endpoint SHALL NOT return HTTP 500

#### Scenario: Supported window values
- **WHEN** the `window` query parameter is one of `1h`, `24h`, `7d`
- **THEN** the endpoint returns aggregates over that window
- **WHEN** an unsupported window value is supplied
- **THEN** the endpoint returns HTTP 400

### Requirement: Available Connectors Discovery Endpoint
The system SHALL expose `GET /api/ingestion/connectors/available` returning the list of connector types and providers the framework can deploy, regardless of whether any instance is currently registered in `connector_registry`.

#### Scenario: Discovery list
- **WHEN** `GET /api/ingestion/connectors/available` is called
- **THEN** the response SHALL include an array of `{ connector_type, channel, provider, display_name, supports_backfill }` entries derived from the framework's known connector registry
- **AND** the response SHALL NOT depend on whether any matching `connector_registry` row exists
- **AND** the response SHALL be safe to cache on the client for at least 60 seconds

#### Scenario: Used by "add connector" UI
- **WHEN** the dashboard renders an "add connector" affordance
- **THEN** it SHALL populate options from this endpoint rather than hardcoded lists

### Requirement: Flame Strip Is Duration-Proportional Approximation
The Timeline UI's flame strip visualization SHALL be specified as a duration-proportional approximation only. It is NOT a token-cost measurement, and the spec and UI tooltip SHALL state this explicitly. Per-step token tracking is an explicit deferral.

#### Scenario: Spec-level labeling
- **WHEN** a downstream spec or API doc references the flame strip
- **THEN** it SHALL be described as a duration-proportional approximation derived from session step durations exposed by the SDK
- **AND** it SHALL NOT be described as token-cost or per-step token accounting

#### Scenario: UI tooltip labeling
- **WHEN** an operator hovers a flame-strip segment in the Timeline UI
- **THEN** the tooltip SHALL state that the visualization is a duration-proportional approximation and NOT a token-cost measurement

### Requirement: Replay History Retention
Replay history (the audit trail of replay attempts, sourced from `audit_log` in v1) SHALL be retained for 90 days, aligned with the retention window for `connectors.filtered_events`. Detailed retention semantics for the underlying storage are delegated to `connector-replay-idempotency-policy`.

#### Scenario: 90-day retention alignment
- **WHEN** replay history is queried for an event older than 90 days
- **THEN** the entries MAY have been pruned by the retention job and SHALL return an empty array (not an error)
- **AND** the retention window SHALL match the retention applied to `connectors.filtered_events`
