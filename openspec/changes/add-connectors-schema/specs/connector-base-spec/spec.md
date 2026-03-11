## ADDED Requirements

### Requirement: Filtered Event Batch Flush Obligation
All connectors SHALL persist filtered and errored events to `connectors.filtered_events` via a batch flush at the end of each poll cycle.

#### Scenario: Connector records filtered events
- **WHEN** a connector filters a message (label exclusion, connector-scope rule, global-scope rule skip)
- **THEN** it SHALL record the event in an in-memory buffer with: connector_type, endpoint_identity, external_message_id, source_channel, sender_identity, subject_or_preview, filter_reason, status='filtered', and full_payload

#### Scenario: Connector records error events
- **WHEN** a connector encounters a processing error for a message (validation failure, submission error)
- **THEN** it SHALL record the event in the buffer with status='error', error_detail containing the exception message, and full_payload containing whatever envelope fields were available

#### Scenario: Flush after poll cycle
- **WHEN** a connector's poll cycle completes
- **THEN** the buffer SHALL be flushed to `connectors.filtered_events` in a single batch INSERT
- **AND** the buffer SHALL be cleared after successful flush
- **AND** flush failure SHALL be logged as a warning but SHALL NOT prevent cursor advancement

### Requirement: Replay Queue Drain Loop
All connectors SHALL check for pending replay requests after each poll cycle and process them through the standard ingestion pipeline.

#### Scenario: Drain loop executes after poll cycle
- **WHEN** a connector completes a poll cycle (including filtered event flush)
- **THEN** it SHALL query `connectors.filtered_events` for rows with `status = 'replay_pending'` matching its `connector_type` and `endpoint_identity`
- **AND** it SHALL process up to 10 replay items per cycle using `FOR UPDATE SKIP LOCKED`

#### Scenario: Replay uses standard ingestion path
- **WHEN** a connector processes a replay item
- **THEN** it SHALL deserialize `full_payload` from the row
- **AND** it SHALL construct a complete `ingest.v1` envelope from the stored payload
- **AND** it SHALL submit the envelope to the Switchboard's `ingest_v1` MCP tool using the same code path as normal ingestion
- **AND** it SHALL NOT re-evaluate connector-side filter rules (the operator explicitly requested replay)

#### Scenario: Replay status update on success
- **WHEN** a replay submission succeeds
- **THEN** the connector SHALL update the row's status to `replay_complete` and set `replay_completed_at`

#### Scenario: Replay status update on failure
- **WHEN** a replay submission fails
- **THEN** the connector SHALL update the row's status to `replay_failed` and set `error_detail` with the failure reason
- **AND** it SHALL continue processing remaining replay items

## MODIFIED Requirements

### Requirement: Connector as Ingestion Primitive
A connector is a long-running process (separate from any butler daemon) that bridges an external messaging system into the butler ecosystem. It is transport-only: read, normalize, filter, submit, checkpoint.

#### Scenario: Connector responsibilities boundary
- **WHEN** a connector processes external events
- **THEN** it reads source events from the external system, normalizes each to an `ingest.v1` envelope, evaluates active source filters (dropping messages that fail the filter gate before any Switchboard call), submits passing envelopes to the Switchboard's canonical ingest API via MCP, persists a crash-safe resume checkpoint, enforces rate limiting against both source API and Switchboard, sends periodic heartbeats for liveness tracking, exports Prometheus metrics, persists filtered/errored events to `connectors.filtered_events` via batch flush, and drains the replay queue for pending re-ingestion requests
- **AND** the connector does NOT classify messages, route to specialist butlers, mint canonical `request_id` values (Switchboard does this), or bypass the Switchboard ingestion path
- **AND** a connector with no active source filters MUST pass all messages (opt-in model; the filter gate is a no-op when no filters are configured)

#### Scenario: Connector as standalone process
- **WHEN** a connector runs
- **THEN** it is a separate OS process from any butler daemon (not an in-daemon module)
- **AND** it communicates with the Switchboard exclusively via MCP tool calls over SSE
- **AND** it has direct database access to the `connectors` schema (for filtered event persistence and replay queue) and read access to the `shared` schema (for credential and contact resolution)

#### Scenario: At-least-once delivery guarantee
- **WHEN** a connector submits events
- **THEN** it guarantees at-least-once delivery via checkpoint-after-acceptance semantics
- **AND** the Switchboard's deduplication layer (advisory lock + dedupe key) makes replays idempotent and harmless
- **AND** duplicate submissions return the same canonical `request_id` (not a new request)
- **AND** messages blocked by source filters are intentionally dropped and their checkpoints advanced; they are NOT retried
- **AND** filtered/errored messages are persisted to `connectors.filtered_events` for operator visibility and optional replay
