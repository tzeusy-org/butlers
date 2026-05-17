## ADDED Requirements

### Requirement: Bulk Replay Endpoint
The system SHALL expose `POST /api/ingestion/events/replay/bulk` accepting a list of `filtered_events` UUIDs and transitioning each eligible row to `replay_pending` in a single transaction using `FOR UPDATE SKIP LOCKED`. The endpoint MUST cap a single request at 50 ids (max-batch-size). It MUST race-safely coexist with `filtered_event_buffer.py:drain` and with concurrent per-event replay requests.

The endpoint delegates per-channel replay safety classification to `connector-replay-idempotency-policy/spec`. In particular: events where `source_channel = 'email'` (or where `connector_registry.replay_safe = false`) MUST be rejected at the handler with HTTP 409 and not transitioned to `replay_pending` until that policy is ratified.

Every bulk replay invocation MUST emit a single `audit.append()` entry with actor, action (`ingestion.replay.bulk_submit` on submission; `ingestion.replay.bulk_reject` if the entire batch is rejected pre-execution), the affected event ids, reason (if provided), and `request_id`. The `ingestion.*` namespace MUST be used consistently across all bulk-replay audit entries.

#### Scenario: Bulk replay with FOR UPDATE SKIP LOCKED
- **WHEN** `POST /api/ingestion/events/replay/bulk` is called with up to 50 valid `filtered_events` UUIDs
- **THEN** the handler SHALL open a single transaction and select the rows with `FOR UPDATE SKIP LOCKED`
- **AND** rows whose current status is `filtered` or `error` SHALL be updated to `replay_pending` with `replay_requested_at = now()`
- **AND** rows that are currently locked by another transaction (e.g., the connector's drain loop) SHALL be skipped with per-id outcome `locked` and NOT block the batch
- **AND** rows whose current status is `replay_pending`, `replay_complete`, or `replay_failed` SHALL be skipped with per-id outcome `not_replayable` and HTTP 409 surfaced in the per-id outcome map (overall response SHALL be HTTP 200)

#### Scenario: Batch size cap enforcement
- **WHEN** `POST /api/ingestion/events/replay/bulk` is called with more than 50 ids
- **THEN** the endpoint SHALL return HTTP 400 with an error naming the 50-id limit
- **AND** no row is mutated

#### Scenario: Email channel blocked at handler
- **WHEN** a bulk request includes a `filtered_events` row whose `source_channel = 'email'`
- **THEN** the handler SHALL reject that id with per-id outcome `channel_not_replay_safe` and HTTP 409 surfaced in the per-id outcome map
- **AND** no `replay_pending` transition SHALL be performed for that id
- **AND** the rejection rationale references `connector-replay-idempotency-policy/spec`

#### Scenario: Replay-unsafe connector blocked at handler
- **WHEN** a bulk request includes a `filtered_events` row whose owning `connector_registry.replay_safe = false`
- **THEN** the handler SHALL reject that id with per-id outcome `channel_not_replay_safe`
- **AND** no `replay_pending` transition SHALL be performed for that id

#### Scenario: No race with drain loop
- **WHEN** the bulk handler runs concurrently with one or more connector `filtered_event_buffer.drain` cycles touching overlapping rows
- **THEN** the use of `FOR UPDATE SKIP LOCKED` SHALL guarantee that each row is mutated by at most one transaction at a time
- **AND** neither transaction blocks waiting on the other; contested rows are skipped (`locked` outcome in the handler, or skipped in the drain cycle, depending on which acquires first)

#### Scenario: Audit log entry on bulk replay
- **WHEN** a bulk replay request completes (fully or partially)
- **THEN** a single `audit.append()` entry is written with action `ingestion.replay.bulk_submit`, the full list of input ids, per-id outcomes, the actor, and `request_id`

#### Scenario: Audit log entry on full batch rejection
- **WHEN** a bulk replay request is rejected pre-execution (e.g. all ids fail validation)
- **THEN** a single `audit.append()` entry is written with action `ingestion.replay.bulk_reject` and the rejection rationale

### Requirement: Replay History Endpoint
The system SHALL expose `GET /api/ingestion/events/:id/replays` returning the replay history for a single `filtered_events` row. In v1 the history is sourced from `audit_log` (no separate table); the response shape is stable regardless of underlying storage, so the source MAY be promoted to a dedicated table if query cost demands it without changing the API contract.

History entries SHALL be retained for 90 days, aligned with the retention window for `connectors.filtered_events`. Older entries return an empty array (not an error).

#### Scenario: Replay history for an event
- **WHEN** `GET /api/ingestion/events/<filtered_event_id>/replays` is called
- **THEN** the response SHALL be an array of `{ requested_at, requested_by, source, outcome, completed_at, error_detail }` entries ordered by `requested_at DESC`
- **AND** `source` is one of `single` (per-event endpoint) or `bulk` (bulk endpoint)
- **AND** `outcome` is one of `pending`, `complete`, `failed`, `superseded`

#### Scenario: Event not found
- **WHEN** the supplied UUID does not match any `filtered_events` row
- **THEN** the endpoint SHALL return HTTP 404

#### Scenario: Event with no replay history
- **WHEN** the row exists but has never been requested for replay
- **THEN** the endpoint SHALL return HTTP 200 with an empty array

#### Scenario: 90-day retention pruning
- **WHEN** a row's replay entries are older than 90 days and have been pruned by the retention job
- **THEN** the endpoint SHALL return HTTP 200 with an empty array
- **AND** the missing entries SHALL NOT cause an error
