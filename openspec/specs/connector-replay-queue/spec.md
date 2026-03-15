# Connector Replay Queue

## Purpose
Defines the replay mechanism for filtered and errored connector events. Replay is modeled as a status transition on `connectors.filtered_events` — the dashboard marks events for replay, and connectors drain pending replays on each poll cycle through the standard ingestion pipeline.

## Requirements

### Requirement: Replay via Status Transition
Replay is modeled as a status transition on `connectors.filtered_events`, not a separate queue table. The dashboard marks events for replay by updating their status; connectors drain pending replays on each poll cycle.

#### Scenario: Replay request from dashboard
- **WHEN** an operator requests replay for a filtered or errored event via the API
- **THEN** the event's status SHALL be updated to `replay_pending` and `replay_requested_at` SHALL be set to the current timestamp
- **AND** the update SHALL only succeed if the current status is `filtered` or `error`
- **AND** if the current status is already `replay_pending`, `replay_complete`, or `replay_failed`, the API SHALL return HTTP 409 Conflict

#### Scenario: Connector drains pending replays
- **WHEN** a connector completes a poll cycle
- **THEN** it SHALL query `connectors.filtered_events` for rows with `status = 'replay_pending'` matching its `connector_type` and `endpoint_identity`, ordered by `received_at ASC`, limited to 10 rows, using `FOR UPDATE SKIP LOCKED`
- **AND** for each row, it SHALL deserialize `full_payload`, wrap it in a `{"schema_version": "ingest.v1", ...}` envelope, and submit to `ingest_v1` via the Switchboard MCP tool

#### Scenario: Successful replay
- **WHEN** a replay submission to `ingest_v1` succeeds (including duplicate=true responses)
- **THEN** the event's status SHALL be updated to `replay_complete` and `replay_completed_at` SHALL be set
- **AND** the event SHALL now appear in `shared.ingestion_events` as a normal ingested event (unless deduplicated)

#### Scenario: Failed replay
- **WHEN** a replay submission to `ingest_v1` raises an exception
- **THEN** the event's status SHALL be updated to `replay_failed`
- **AND** `error_detail` SHALL be updated with the failure reason
- **AND** the connector SHALL continue processing remaining replay items (fail-one, not fail-all)

#### Scenario: Deduplication safety for filtered events
- **WHEN** a filtered event is replayed
- **THEN** no prior dedupe record exists in `shared.ingestion_events` (because the message was never submitted to `ingest_v1`)
- **AND** `ingest_v1` SHALL accept it as a new event and create a normal `ingestion_events` row

#### Scenario: Deduplication safety for error events
- **WHEN** an error event is replayed (one that failed validation before DB insert)
- **THEN** no prior dedupe record exists (validation errors are raised before the INSERT)
- **AND** `ingest_v1` SHALL accept it as a new event

#### Scenario: Replay of already-ingested event (edge case)
- **WHEN** an event was partially processed (accepted by `ingest_v1` but failed downstream)
- **THEN** the dedupe key SHALL match the existing `ingestion_events` row
- **AND** `ingest_v1` SHALL return `duplicate=true`
- **AND** the replay status SHALL transition to `replay_complete` (harmless no-op)

### Requirement: Replay API Endpoint
A REST endpoint allows the dashboard to request replay for a specific filtered event.

#### Scenario: POST replay request
- **WHEN** `POST /api/ingestion/events/{id}/replay` is called with a valid filtered_events UUID
- **THEN** the endpoint SHALL update the row's status to `replay_pending`
- **AND** return HTTP 200 with `{"status": "replay_pending", "id": "<uuid>"}`

#### Scenario: Event not found
- **WHEN** `POST /api/ingestion/events/{id}/replay` is called with an unknown UUID
- **THEN** the endpoint SHALL return HTTP 404

#### Scenario: Event not replayable
- **WHEN** `POST /api/ingestion/events/{id}/replay` is called for an event with status `replay_pending`, `replay_complete`, or `replay_failed`
- **THEN** the endpoint SHALL return HTTP 409 Conflict with `{"error": "Event is not replayable", "current_status": "<status>"}`

#### Scenario: Replay of replay_failed event
- **WHEN** `POST /api/ingestion/events/{id}/replay` is called for an event with status `replay_failed`
- **THEN** the endpoint SHALL allow re-replay by updating status back to `replay_pending`
- **AND** `replay_requested_at` SHALL be updated to the current timestamp
- **AND** `error_detail` SHALL be cleared
