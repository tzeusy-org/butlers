## ADDED Requirements

### Requirement: Filtered-Content Privacy Tier
Content that a connector deliberately declines to submit to the Switchboard SHALL be persisted under a minimal-retention privacy tier: the connector chose not to process it, so its full raw payload MUST NOT be retained. This tier applies to every `filtered`-status row regardless of connector or filter reason. Errored rows (`error` status) are exempt — a processing failure is not a discretion decision, and its payload is retained for diagnosis and replay.

This resolves a real divergence between connectors: the WhatsApp user-client persisted filtered content as an empty raw payload plus a bounded preview, while several other connectors (Telegram user-client, Gmail, Discord, Google Calendar, Google Health, Spotify, Telegram bot) persisted the full raw provider payload for the same class of dropped content. The minimal-retention posture is now normative.

#### Scenario: Filtered content persists a bounded preview only
- **WHEN** a connector persists a row with status `filtered` (any filter reason: `label_exclude:*`, a policy-rule reason, or `discretion:ignore:*`)
- **THEN** `subject_or_preview` SHALL contain at most 200 characters of the message's normalized text, or NULL when no text is available
- **AND** `full_payload.payload.raw` SHALL be empty (`{}`) — the full raw provider payload MUST NOT be persisted
- **AND** the envelope metadata (`source`, `event`, `sender`, `control`) SHALL still be persisted for operator visibility and audit

#### Scenario: Errored content is exempt from the privacy tier
- **WHEN** a connector persists a row with status `error`
- **THEN** `full_payload.payload.raw` MAY contain the raw provider payload available at the point of failure
- **AND** this content is retained to support diagnosis and replay, not redacted for privacy

#### Scenario: Replay of filtered content is best-effort
- **WHEN** a `filtered`-status row is replayed through the ingestion pipeline
- **THEN** the replay envelope is reconstructed from the persisted metadata (and bounded preview) only
- **AND** because the raw payload was not retained, replay fidelity is best-effort and MAY differ from the original message body

## MODIFIED Requirements

### Requirement: Connectors Schema
The `connectors` Postgres schema is a dedicated namespace for connector-owned persistent state. It is separate from the `switchboard` schema and SHALL be owned by connector processes.

#### Scenario: Schema exists at startup
- **WHEN** the butler database is initialized
- **THEN** the `connectors` schema SHALL exist
- **AND** connector processes SHALL have USAGE and CREATE privileges on the `connectors` schema
- **AND** connector processes SHALL have SELECT privileges on the `public` schema

### Requirement: Filtered Events Table
The `connectors.filtered_events` table persists every message a connector observes but does not submit to the Switchboard — one row per filtered or errored message. Errored rows SHALL persist the full available payload for replay; filtered rows SHALL persist a bounded preview with the raw payload redacted (see the Filtered-Content Privacy Tier requirement).

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
- **THEN** the event metadata and payload SHALL be recorded in an in-memory buffer, with the payload's raw content governed by the Filtered-Content Privacy Tier requirement
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

#### Scenario: Filter reason for discretion IGNORE
- **WHEN** a message is dropped by the connector's discretion layer (an LLM-judged or fail-closed IGNORE verdict)
- **THEN** `filter_reason` SHALL be `discretion:ignore:<kind>`, where `<kind>` classifies the cause
- **AND** `<kind>` SHALL be one of: `llm_verdict` (a genuine model IGNORE), `auth_failure_default`, `provider_unavailable_default`, `failover_exhausted`, `timeout_default`, `parse_error_default`, or `error_default` (fail-closed defaults emitted when the discretion dispatcher could not render a verdict)
- **AND** a bare `discretion:IGNORE` reason SHALL NOT be used — the `<kind>` suffix is required so a genuine IGNORE can be distinguished from a fail-closed default drop without re-sampling the raw payloads

#### Scenario: Filter reason for validation errors
- **WHEN** a message fails envelope validation or Switchboard submission
- **THEN** `filter_reason` SHALL be `validation_error` or `submission_error`
- **AND** `error_detail` SHALL contain the exception message or validation error text
- **AND** `status` SHALL be `error` (not `filtered`)

### Requirement: Full Payload Shape
The `full_payload` JSONB column SHALL store envelope metadata sufficient to reconstruct an `ingest.v1` envelope shape. For errored rows it additionally retains the raw provider payload for full-fidelity replay; for filtered rows the raw payload is redacted per the Filtered-Content Privacy Tier requirement, so replay of filtered rows is best-effort (metadata plus bounded preview only).

#### Scenario: Payload contains envelope fields
- **WHEN** a filtered event is persisted
- **THEN** `full_payload` SHALL contain the keys: `source` (channel, provider, endpoint_identity), `event` (external_event_id, external_thread_id, observed_at), `sender` (identity), `payload` (raw, normalized_text), and `control` (policy_tier)
- **AND** `schema_version` SHALL be omitted (always `ingest.v1` on replay)
- **AND** for rows with status `filtered`, `full_payload.payload.raw` SHALL be empty (`{}`) — the full raw provider payload is not retained

#### Scenario: Payload for error status
- **WHEN** a message fails with status `error`
- **THEN** `full_payload` SHALL contain whatever envelope fields were available at the point of failure
- **AND** incomplete payloads are acceptable — replay of error-status events MAY fail again if the root cause is not fixed

## Source References
- `about/heart-and-soul/security.md` — "Sensitive Data Categories": connector-level controls (ingestion tier, retention periods, opt-in activation) are the primary privacy mechanism. The metadata-tier precedent (OwnTracks stores "no raw coordinates in the ingest payload") is the same posture applied here to deliberately-filtered content.
- RFC 0003 (Switchboard routing and ingestion) — `ingest.v1` envelope shape reconstructed on replay.
- `openspec/specs/connector-live-listener/spec.md` — ambient-capture connector already applies this privacy tier for discretion-IGNORE content (`raw={}` + bounded preview).
