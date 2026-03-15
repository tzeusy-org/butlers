## MODIFIED Requirements

### Requirement: Deduplication Strategy
The Switchboard computes a stable deduplication key for each ingest submission using a priority-based strategy. Advisory locking prevents race conditions on concurrent submissions with the same key.

#### Scenario: Priority 1 — Explicit idempotency key
- **WHEN** `control.idempotency_key` is provided
- **THEN** the dedupe key is `"idem:{channel}:{endpoint_identity}:{idempotency_key}"`

#### Scenario: Priority 2 — External event ID
- **WHEN** no explicit idempotency key is provided and `event.external_event_id` is non-placeholder (not `"placeholder"`, `"unknown"`, `"none"`, or empty)
- **THEN** the dedupe key is `"event:{channel}:{provider}:{endpoint_identity}:{external_event_id}"`

#### Scenario: Priority 3 — Content hash fallback
- **WHEN** neither explicit key nor usable event ID is available
- **THEN** the dedupe key is `"hash:{channel}:{endpoint_identity}:{sender}:{hour_bucket}:{content_hash[:16]}"` where `content_hash` is SHA256 of `normalized_text:sender_identity` and `hour_bucket` is the hourly time window (`YYYYMMDDHH`)

#### Scenario: Advisory lock serialization
- **WHEN** the Switchboard processes an ingest submission
- **THEN** it acquires `pg_advisory_xact_lock(hashtext(dedupe_key))` within a transaction
- **AND** inside the lock: re-checks for an existing record with the same dedupe key, and either returns the existing `request_id` with `duplicate=true` or inserts a new row into both `message_inbox` and `shared.ingestion_events` atomically within the same transaction

#### Scenario: Ingest accepted response
- **WHEN** the Switchboard accepts an ingest submission
- **THEN** it returns `IngestAcceptedResponse` with: `request_id` (UUID7, canonical reference), `status` (`"accepted"`), `duplicate` (bool), `triage_decision` (string or None), `triage_target` (butler name or None)

### Requirement: Request Context Assignment
The Switchboard builds an immutable request context from each accepted ingest envelope. This context travels with the message through classification, routing, and butler processing. The `request_id` is the UUID7 primary key of the corresponding `shared.ingestion_events` row.

#### Scenario: Request context fields
- **WHEN** a message is accepted for processing
- **THEN** the Switchboard assigns: `request_id` (UUID7, equals `shared.ingestion_events.id`), `received_at` (server timestamp), `source_channel`, `source_endpoint_identity`, `source_sender_identity`, `source_thread_identity` (from `external_thread_id`), `idempotency_key`, `trace_context`, `ingestion_tier`, `dedupe_key`, `dedupe_strategy` (`"connector_api"`)
- **AND** if triage was evaluated: `triage_decision`, `triage_target`, `triage_rule_id`, `triage_rule_type`
- **AND** the `request_id` is passed through to the spawned butler session as both `session.request_id` and `session.ingestion_event_id`
