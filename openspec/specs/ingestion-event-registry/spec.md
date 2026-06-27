# Ingestion Event Registry

## Purpose
Provides the canonical first-class record of every event that enters the butler ecosystem via a connector. The `public.ingestion_events` table anchors request-ID-based lineage queries across all downstream sessions and traces, replacing the previous trace-ID-based lookup model.

## Requirements

### Requirement: Ingestion Event Table
The `public.ingestion_events` table is the canonical first-class record of every event that enters the butler ecosystem via a connector. One row exists per canonical ingestion event after deduplication. The UUID7 primary key is the `request_id` returned to connectors and propagated to all downstream sessions and traces.

#### Scenario: Row created on new ingest accept
- **WHEN** the Switchboard accepts an ingest envelope and no existing row matches the computed dedupe key
- **THEN** a new row is inserted into `public.ingestion_events` inside the same advisory-lock transaction used for deduplication, with fields: `id` (UUID7), `received_at` (server timestamp), `source_channel`, `source_provider`, `source_endpoint_identity`, `source_sender_identity`, `source_thread_identity`, `external_event_id`, `dedupe_key`, `dedupe_strategy`, `ingestion_tier`, `policy_tier`, `triage_decision`, and `triage_target`
- **AND** the row's `id` is returned as `request_id` in `IngestAcceptedResponse`

#### Scenario: Duplicate submission returns existing row ID
- **WHEN** the Switchboard receives an envelope whose computed dedupe key matches an existing `public.ingestion_events` row
- **THEN** no new row is inserted
- **AND** the existing row's `id` is returned as `request_id` with `duplicate=true`

#### Scenario: UUID7 is time-ordered
- **WHEN** two ingestion events are accepted in sequence
- **THEN** the second event's `id` is lexicographically greater than the first's
- **AND** UUID7 ordering can substitute for a separate `received_at` index for recency queries

### Requirement: Ingestion Event Query by ID
Fetch a single ingestion event record by its UUID7 primary key.

#### Scenario: Successful lookup
- **WHEN** `ingestion_event_get(pool, event_id)` is called with a valid UUID7
- **THEN** the full ingestion event record is returned with all persisted fields

#### Scenario: Missing event
- **WHEN** `ingestion_event_get(pool, event_id)` is called with an unknown UUID7
- **THEN** `None` is returned (no exception raised)

### Requirement: Ingestion Event List (Paginated)
Return a unified stream of all ingestion events (ingested, filtered, errored) ordered by `received_at DESC, id DESC` using keyset (cursor) pagination, with optional filtering. The function returns a dict with `items`, `next_cursor` (opaque, or null on the last page), and `has_more`. There is no `offset` or `total`. A `sort="cost"` mode orders by `cost_usd DESC NULLS LAST` and pages via an opaque offset-encoding cursor.

#### Scenario: Paginated list
- **WHEN** `ingestion_events_list(pool, limit=20, cursor=None)` is called
- **THEN** up to 20 rows are returned (most recent first), with an opaque `next_cursor` for the following page
- **AND** the result SHALL include events from both `public.ingestion_events` and `connectors.filtered_events` merged by `received_at DESC`

#### Scenario: Filtered by source channel
- **WHEN** `ingestion_events_list(pool, source_channel="email")` is called
- **THEN** only events with `source_channel = 'email'` are returned from both tables

#### Scenario: Response includes status field
- **WHEN** the unified list is returned
- **THEN** each row SHALL include a `status` field. For `public.ingestion_events` rows this is `ingested`, `failed`, or `replay_pending` (and the synthetic `skipped`, surfaced when `status='ingested'` and `triage_decision='skip'`). For `connectors.filtered_events` rows it is the `status` column value (`filtered`, `error`, `replay_pending`, `replay_complete`, `replay_failed`)

#### Scenario: Response includes filter_reason field
- **WHEN** the unified list is returned
- **THEN** each row SHALL include a `filter_reason` field: `null` for ingested events, or the `filter_reason` column value for filtered/errored events

#### Scenario: Filtered by status
- **WHEN** `ingestion_events_list(pool, status="filtered")` is called
- **THEN** only events with the matching status are returned
- **AND** `status="ingested"` queries only `public.ingestion_events`
- **AND** all other status values query only `connectors.filtered_events`

### Requirement: Session Lineage Query
Return all sessions spawned from a given `request_id`, joined across all butler schemas. Works for both connector-sourced events (via `ingestion_event_id` FK) and internally-minted request IDs (via direct `request_id` match).

#### Scenario: Lineage for a connector-sourced event
- **WHEN** `ingestion_event_sessions(db, request_id, pricing=None)` is called (where `db` is a DatabaseManager that fans out across all butler schemas) with a UUID7 that has a corresponding `public.ingestion_events` row
- **THEN** all session rows where `ingestion_event_id = request_id` are returned across all butler schemas, ordered by `started_at ASC`
- **AND** each row includes `butler_name`, `id`, `trigger_source`, `started_at`, `completed_at`, `success`, `input_tokens`, `output_tokens`, `cost`, `trace_id`, `model`, and a computed `cost_usd`

#### Scenario: Lineage for an internally-minted request ID
- **WHEN** `ingestion_event_sessions(db, request_id, pricing=None)` is called with a UUID7 minted for an internal session (no `public.ingestion_events` row)
- **THEN** all sessions where `request_id` matches are returned (the query falls back to direct `request_id` match when no ingestion event row exists)

### Requirement: Dashboard Channel as Valid Ingestion Source
The `public.ingestion_events` table accepts events with `source_channel = "dashboard"`. Dashboard-originated events follow the same deduplication, request context, and lineage semantics as any connector-originated event.

#### Scenario: Dashboard ingestion event recorded
- **WHEN** a dashboard conversation message is ingested by the Switchboard
- **THEN** a row is inserted into `public.ingestion_events` with `source_channel = "dashboard"`, `source_provider = "internal"`, and `source_endpoint_identity = "dashboard:web:{conversation_id}"`
- **AND** the `request_id` is returned and propagated to the resulting butler session

#### Scenario: Dashboard events in unified ingestion list
- **WHEN** `ingestion_events_list(pool)` is called without filters
- **THEN** dashboard-originated events appear alongside connector-originated events in the unified stream
- **AND** they can be filtered with `source_channel = "dashboard"`

#### Scenario: Dashboard event lineage
- **WHEN** `ingestion_event_sessions(db, request_id, pricing=None)` is called for a dashboard-originated event
- **THEN** the resulting butler session(s) are returned with `trigger_source = "dashboard"` in the lineage

### Requirement: Token and Cost Rollup per Request ID
Aggregate token usage and cost across all sessions attributed to a single `request_id`.

#### Scenario: Rollup for a request ID
- **WHEN** `ingestion_event_rollup(request_id, sessions, pricing=None)` is called (synchronous; it aggregates the session list returned by `ingestion_event_sessions`, it does not query the database itself)
- **THEN** the result includes `total_sessions`, `total_input_tokens`, `total_output_tokens`, `total_cost`, and a `by_butler` breakdown with per-butler token and cost totals
- **AND** the rollup covers all sessions with `request_id` equal to the given value regardless of whether an `ingestion_events` row exists
