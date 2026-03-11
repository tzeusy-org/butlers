## ADDED Requirements

### Requirement: Connectors Schema
The `connectors` Postgres schema is a dedicated namespace for connector-owned persistent state. It is separate from the `switchboard` schema and owned by connector processes.

#### Scenario: Schema exists at startup
- **WHEN** the butler database is initialized
- **THEN** the `connectors` schema SHALL exist
- **AND** connector processes SHALL have USAGE and CREATE privileges on the `connectors` schema
- **AND** connector processes SHALL have SELECT privileges on the `shared` schema

### Requirement: Filtered Events Table
The `connectors.filtered_events` table persists every message a connector observes but does not submit to the Switchboard. One row per filtered or errored message, with full payload for replay.

#### Scenario: Table structure
- **WHEN** the `connectors.filtered_events` table is created
- **THEN** it SHALL contain columns: `id` (UUID, primary key), `received_at` (timestamptz, not null, default now()), `connector_type` (text, not null), `endpoint_identity` (text, not null), `external_message_id` (text, not null), `source_channel` (text, not null), `sender_identity` (text, not null), `subject_or_preview` (text, nullable), `filter_reason` (text, not null), `status` (text, not null, default 'filtered'), `full_payload` (jsonb, not null), `error_detail` (text, nullable), `replay_requested_at` (timestamptz, nullable), `replay_completed_at` (timestamptz, nullable), `created_at` (timestamptz, not null, default now())
- **AND** the table SHALL be partitioned by RANGE on `received_at`

#### Scenario: Monthly partitioning
- **WHEN** a filtered event is inserted
- **THEN** the partition for the event's `received_at` month SHALL exist or be auto-created
- **AND** partition naming SHALL follow the pattern `filtered_events_YYYYMM`

#### Scenario: Retention policy
- **WHEN** partitions older than 90 days exist
- **THEN** they MAY be dropped by a scheduled maintenance task
- **AND** the retention period SHALL be configurable

#### Scenario: Status values
- **WHEN** a filtered event row exists
- **THEN** its `status` column SHALL be one of: `filtered` (connector-side filter applied), `error` (connector-side processing error, e.g. validation failure), `replay_pending` (replay requested, awaiting connector pickup), `replay_complete` (replay submitted to Switchboard successfully), `replay_failed` (replay attempted but failed)

### Requirement: Filtered Event Persistence (Batch Flush)
Connectors SHALL accumulate filtered events in memory during each poll cycle and flush them to the database in a single batch INSERT after the cycle completes.

#### Scenario: Batch accumulation during poll cycle
- **WHEN** a connector filters or errors on a message during a poll cycle
- **THEN** the event metadata and full payload SHALL be recorded in an in-memory buffer
- **AND** no database write SHALL occur until the poll cycle completes

#### Scenario: Batch flush after poll cycle
- **WHEN** a connector's poll cycle completes (all messages processed, cursor advanced)
- **THEN** all buffered filtered events SHALL be flushed to `connectors.filtered_events` in a single batch INSERT
- **AND** the buffer SHALL be cleared after successful flush

#### Scenario: Crash before flush
- **WHEN** a connector crashes mid-poll-cycle before flushing
- **THEN** unflushed filtered events from that cycle are lost
- **AND** this is acceptable because filtered events are operational visibility data, not audit trail

#### Scenario: Filter reason format
- **WHEN** a message is filtered by label exclusion
- **THEN** `filter_reason` SHALL be `label_exclude:<label_name>` (e.g. `label_exclude:CATEGORY_PROMOTIONS`)

#### Scenario: Filter reason for policy rules
- **WHEN** a message is filtered by an ingestion policy rule
- **THEN** `filter_reason` SHALL be `<scope>:<action>:<rule_type>` (e.g. `global_rule:skip:sender_domain`)

#### Scenario: Filter reason for validation errors
- **WHEN** a message fails envelope validation or Switchboard submission
- **THEN** `filter_reason` SHALL be `validation_error` or `submission_error`
- **AND** `error_detail` SHALL contain the exception message or validation error text
- **AND** `status` SHALL be `error` (not `filtered`)

### Requirement: Full Payload Shape
The `full_payload` JSONB column SHALL store enough data to reconstruct an `ingest.v1` envelope without re-fetching from the external API.

#### Scenario: Payload contains envelope fields
- **WHEN** a filtered event is persisted
- **THEN** `full_payload` SHALL contain the keys: `source` (channel, provider, endpoint_identity), `event` (external_event_id, external_thread_id, observed_at), `sender` (identity), `payload` (raw, normalized_text), and `control` (policy_tier)
- **AND** `schema_version` SHALL be omitted (always `ingest.v1` on replay)

#### Scenario: Payload for error status
- **WHEN** a message fails with status `error`
- **THEN** `full_payload` SHALL contain whatever envelope fields were available at the point of failure
- **AND** incomplete payloads are acceptable — replay of error-status events MAY fail again if the root cause is not fixed
