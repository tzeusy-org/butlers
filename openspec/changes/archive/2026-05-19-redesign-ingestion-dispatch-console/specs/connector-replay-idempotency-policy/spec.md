# Connector Replay Idempotency Policy

## Purpose
Defines the per-channel replay-safety classification for ingestion events and the concurrency contract for the bulk-replay handler. Replay is a privileged operator action that re-injects a previously filtered or errored event back through the standard ingestion pipeline. Without a channel-aware safety gate, replay on a non-idempotent channel (e.g. Gmail outbound reply) can cause user-visible side effects (duplicate emails). This capability owns the safety classification, the replay-safe registry flag, and the bulk-handler concurrency contract. It complements `connector-replay-queue` (which owns the queue mechanics) and `connector-filtered-events` (which owns the source-of-truth event rows).

## ADDED Requirements

### Requirement: Per-channel replay safety classification
The system SHALL classify every `source_channel` as either replay-safe or replay-unsafe. The default classification per known channel SHALL be:

- `telegram` (receive) — replay-safe
- `email` (Gmail receive triggering a reply) — replay-unsafe
- webhook channels — replay-safe unless the connector's `connector_registry.replay_safe` column is explicitly set to FALSE

Any channel not enumerated above SHALL default to replay-safe only if the connector's `replay_safe` registry column is TRUE. Operators SHALL NOT replay events on a channel classified as replay-unsafe through this surface.

#### Scenario: Telegram receive is replay-safe
- **WHEN** an event with `source_channel = 'telegram'` is submitted to the bulk-replay handler
- **THEN** the handler accepts the event for replay (subject to other gates)

#### Scenario: Gmail reply is replay-unsafe
- **WHEN** an event with `source_channel = 'email'` is submitted to the bulk-replay handler
- **THEN** the handler rejects the event with HTTP 409
- **AND** the response body identifies `source_channel = 'email'` as the unsafe-channel block reason

#### Scenario: Webhook channel honors registry flag
- **WHEN** an event with `source_channel = '<webhook>'` is submitted and the matching `connector_registry` row has `replay_safe = FALSE`
- **THEN** the handler rejects the event with HTTP 409

### Requirement: connector_registry.replay_safe column
The `connector_registry` table SHALL include a column `replay_safe BOOLEAN NOT NULL DEFAULT TRUE`. This column SHALL be settable by the registry migration or by operator update through the connector registry surface (not exposed for end-user UI editing in v1). The bulk-replay handler SHALL read this flag for every event being considered for replay.

#### Scenario: Migration adds replay_safe column
- **WHEN** the migration that introduces this spec runs
- **THEN** `connector_registry.replay_safe` exists with `DEFAULT TRUE` and `NOT NULL`
- **AND** existing rows have `replay_safe = TRUE` after migration

#### Scenario: Gmail registry row is set to FALSE
- **WHEN** the migration completes
- **THEN** any `connector_registry` row whose `connector_type` corresponds to Gmail SHALL have `replay_safe = FALSE`

### Requirement: Bulk replay concurrency contract
The bulk-replay handler SHALL select candidate filtered-event rows using `SELECT ... FOR UPDATE SKIP LOCKED` and SHALL cap each batch to a maximum of 50 rows. This is a correctness requirement — without `FOR UPDATE SKIP LOCKED` the handler races against `filtered_event_buffer.py:drain` and the connector's replay-drain loop. The handler SHALL NOT exceed the 50-row max per request, regardless of operator input.

#### Scenario: FOR UPDATE SKIP LOCKED applied
- **WHEN** the bulk-replay handler selects candidate rows
- **THEN** the SELECT statement uses `FOR UPDATE SKIP LOCKED` on `connectors.filtered_events`
- **AND** rows currently locked by another transaction (e.g. the connector drain loop) are skipped without blocking

#### Scenario: Batch size capped at 50
- **WHEN** the bulk-replay handler receives a request specifying more than 50 event ids
- **THEN** the handler processes only the first 50 (in submission order)
- **AND** the response indicates the cap was applied and identifies the unprocessed ids

#### Scenario: Batch size below cap honored as-is
- **WHEN** the bulk-replay handler receives a request specifying fewer than 50 event ids
- **THEN** all submitted ids are considered for replay (subject to per-channel safety gating)

### Requirement: HTTP 409 on unsafe-channel replay attempt
The bulk-replay handler SHALL return HTTP 409 (Conflict) when any event in the submitted batch has a `source_channel` classified as replay-unsafe under this spec. The 409 response SHALL identify the offending events and the unsafe channel by name. The handler SHALL NOT partially process the batch when at least one event is unsafe — the entire batch is rejected to preserve atomic semantics for the operator.

#### Scenario: Mixed batch with one email event
- **WHEN** the handler receives a batch containing 10 events of which one has `source_channel = 'email'`
- **THEN** no events are replayed
- **AND** the handler returns 409 with a body identifying the email event id and the unsafe channel

#### Scenario: All-safe batch proceeds
- **WHEN** the handler receives a batch in which all events have replay-safe channels
- **THEN** the handler proceeds to mark the events `status = 'replay_pending'` (subject to the concurrency contract above)

### Requirement: Audit emission on bulk replay
The bulk-replay handler SHALL emit one `audit.append()` entry per submitted batch to `public.audit_log` with `actor`, `action = 'ingestion.replay.bulk_submit'`, `target` listing the event ids accepted, `reason` (operator-supplied free text), and `request_id`. Rejected batches (HTTP 409) SHALL also emit an audit entry recording the rejection with `action = 'ingestion.replay.bulk_reject'` and the unsafe-channel reason.

#### Scenario: Successful bulk replay audited
- **WHEN** the bulk-replay handler accepts a batch
- **THEN** a single audit entry is written with the actor, the accepted event ids, the reason, and the request_id

#### Scenario: Rejected bulk replay audited
- **WHEN** the bulk-replay handler rejects a batch with HTTP 409
- **THEN** a single audit entry is written with `action = 'ingestion.replay.bulk_reject'` and the unsafe-channel block reason

### Requirement: 90-day replay history retention
Replay-history records (whether sourced from `connectors.filtered_events.replay_*` columns or from a future dedicated replay-history surface) SHALL be retained for 90 days from the original `received_at` of the underlying event. The retention window SHALL align with the existing `filtered_events` retention so that replay lineage and event payload age out together.

#### Scenario: Replay history aligns with filtered_events
- **WHEN** the retention sweep runs against `connectors.filtered_events`
- **THEN** rows older than 90 days are removed
- **AND** any replay-history projection or read endpoint based on those rows reflects the deletion (no orphan replay records past 90 days)

#### Scenario: Audit log retention is unaffected
- **WHEN** the 90-day sweep deletes replay-history rows
- **THEN** the corresponding `public.audit_log` entries are NOT deleted (audit retention is indefinite)
