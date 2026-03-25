# Ingestion Event Registry (Delta)

## ADDED Requirements

### Requirement: Dashboard Channel as Valid Ingestion Source
The `shared.ingestion_events` table accepts events with `source_channel = "dashboard"`. Dashboard-originated events follow the same deduplication, request context, and lineage semantics as any connector-originated event.

#### Scenario: Dashboard ingestion event recorded
- **WHEN** a dashboard conversation message is ingested by the Switchboard
- **THEN** a row is inserted into `shared.ingestion_events` with `source_channel = "dashboard"`, `source_provider = "internal"`, and `source_endpoint_identity = "dashboard:web:{conversation_id}"`
- **AND** the `request_id` is returned and propagated to the resulting butler session

#### Scenario: Dashboard events in unified ingestion list
- **WHEN** `ingestion_events_list(pool)` is called without filters
- **THEN** dashboard-originated events appear alongside connector-originated events in the unified stream
- **AND** they can be filtered with `source_channel = "dashboard"`

#### Scenario: Dashboard event lineage
- **WHEN** `ingestion_event_sessions(pool, request_id)` is called for a dashboard-originated event
- **THEN** the resulting butler session(s) are returned with `trigger_source = "dashboard"` in the lineage
