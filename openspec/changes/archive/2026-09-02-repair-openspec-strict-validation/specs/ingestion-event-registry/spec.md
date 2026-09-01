## MODIFIED Requirements

### Requirement: Ingestion Event Query by ID

The implementation SHALL provide the behavior described by this requirement.
Fetch a single ingestion event record by its UUID7 primary key.

#### Scenario: Successful lookup
- **WHEN** `ingestion_event_get(pool, event_id)` is called with a valid UUID7
- **THEN** the full ingestion event record is returned with all persisted fields

#### Scenario: Missing event
- **WHEN** `ingestion_event_get(pool, event_id)` is called with an unknown UUID7
- **THEN** `None` is returned (no exception raised)

### Requirement: Ingestion Event List (Paginated)

The implementation SHALL provide the behavior described by this requirement.
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

The implementation SHALL provide the behavior described by this requirement.
Return all sessions spawned from a given `request_id`, joined across all butler schemas. Works for both connector-sourced events (via `ingestion_event_id` FK) and internally-minted request IDs (via direct `request_id` match).

#### Scenario: Lineage for a connector-sourced event
- **WHEN** `ingestion_event_sessions(db, request_id, pricing=None)` is called (where `db` is a DatabaseManager that fans out across all butler schemas) with a UUID7 that has a corresponding `public.ingestion_events` row
- **THEN** all session rows where `ingestion_event_id = request_id` are returned across all butler schemas, ordered by `started_at ASC`
- **AND** each row includes `butler_name`, `id`, `trigger_source`, `started_at`, `completed_at`, `success`, `input_tokens`, `output_tokens`, `cost`, `trace_id`, `model`, and a computed `cost_usd`

#### Scenario: Lineage for an internally-minted request ID
- **WHEN** `ingestion_event_sessions(db, request_id, pricing=None)` is called with a UUID7 minted for an internal session (no `public.ingestion_events` row)
- **THEN** all sessions where `request_id` matches are returned (the query falls back to direct `request_id` match when no ingestion event row exists)

### Requirement: Dashboard Channel as Valid Ingestion Source

The implementation SHALL provide the behavior described by this requirement.
The `public.ingestion_events` table accepts events with `source_channel = "dashboard"`. Dashboard-originated events follow the same deduplication, request-context, and lineage semantics as connector-originated events without becoming connector provenance or acquiring connector-specific filtered-event/status semantics.

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
- **THEN** the resulting routed target-butler session(s) are returned with
  `trigger_source = "route"` in the lineage
- **AND** the originating ingestion event retains `source_channel = "dashboard"`
  and `source_provider = "internal"`; ingress provenance and the downstream
  session trigger boundary are distinct
