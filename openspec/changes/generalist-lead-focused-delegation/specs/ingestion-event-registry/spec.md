## MODIFIED Requirements

### Requirement: Session Lineage Query

The function SHALL return the lineage of a given `request_id`: the goals admitted from it with each goal's lead and child invocations, read from the goal ledger, plus any sessions spawned from it before cutover, joined across all butler schemas. Invocation lineage SHALL come from the goal ledger (`goal_id`, `invocation_id`, `depth`), never from a routed session's `trigger_source`. Works for both connector-sourced events (via `ingestion_event_id` FK) and internally-minted request IDs (via direct `request_id` match).

Source: RFC 0038 D5, D17
Scope: v1-mandatory

#### Scenario: Lineage for a connector-sourced event
- **WHEN** `ingestion_event_sessions(db, request_id, pricing=None)` is called (where `db` is a DatabaseManager that fans out across all butler schemas) with a UUID7 that has a corresponding `public.ingestion_events` row
- **THEN** the goal admitted from that envelope and all of its invocations are returned, ordered by `started_at ASC`
- **AND** each invocation row includes `goal_id`, `invocation_id`, `depth`, `started_at`, `completed_at`, `success`, `input_tokens`, `output_tokens`, `cost`, `trace_id`, `model`, and a computed `cost_usd`

#### Scenario: Lineage for an internally-minted request ID
- **WHEN** `ingestion_event_sessions(db, request_id, pricing=None)` is called with a UUID7 minted for an internal admission (no `public.ingestion_events` row)
- **THEN** the goals whose admission carries that `request_id` and their invocations are returned (the query falls back to direct `request_id` match when no ingestion event row exists)

#### Scenario: Pre-cutover lineage stays readable
- **WHEN** the lineage of a `request_id` ingested before cutover is requested
- **THEN** the routed sessions recorded then are returned with their stored `butler_name` and `trigger_source`

### Requirement: Dashboard Channel as Valid Ingestion Source

The `public.ingestion_events` table SHALL accept events with `source_channel = "dashboard"`. Dashboard-originated events follow the same deduplication, request-context, and lineage semantics as connector-originated events without becoming connector provenance or acquiring connector-specific filtered-event/status semantics; each one SHALL be submitted to goal admission rather than routed to a target butler.

Source: RFC 0038 D5
Scope: v1-mandatory

#### Scenario: Dashboard ingestion event recorded
- **WHEN** a dashboard conversation message is ingested by the Switchboard
- **THEN** a row is inserted into `public.ingestion_events` with `source_channel = "dashboard"`, `source_provider = "internal"`, and `source_endpoint_identity = "dashboard:web:{conversation_id}"`
- **AND** the `request_id` is returned and propagated to the goal admitted from it

#### Scenario: Dashboard events in unified ingestion list
- **WHEN** `ingestion_events_list(pool)` is called without filters
- **THEN** dashboard-originated events appear alongside connector-originated events in the unified stream
- **AND** they can be filtered with `source_channel = "dashboard"`

#### Scenario: Dashboard event lineage
- **WHEN** `ingestion_event_sessions(db, request_id, pricing=None)` is called for a dashboard-originated event
- **THEN** the owner goal admitted from it and that goal's invocations are returned in the lineage
- **AND** the originating ingestion event retains `source_channel = "dashboard"`
  and `source_provider = "internal"`; ingress provenance and the admitted
  goal's origin are distinct
