# Connector Filtered Events

## MODIFIED Requirements

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
- **WHEN** partitions older than the configured keep window exist
- **THEN** they MAY be dropped by a scheduled maintenance task
- **AND** the retention period SHALL be configurable
- **AND** the shipped default keep window SHALL be 12 months, not 90 days
- **AND** the sweep SHALL NOT delete anything unless it is explicitly scheduled, explicitly enabled, and explicitly taken out of dry-run
- **AND** in the shipped configuration none of those three conditions holds, so no partition is ever dropped and the table grows without bound

#### Scenario: No partition is dropped in the shipped configuration
- **WHEN** the system runs with the roster configuration as shipped
- **THEN** no butler schedules the `filtered_events_partition_prune` job, so it is never invoked
- **AND** the pruner's `enabled` parameter SHALL default to false, returning without a database call when unset
- **AND** the pruner's `dry_run` parameter SHALL default to true, so an enabled invocation counts candidates instead of dropping them
- **AND** the honest description of the table's retention today is keep-forever, with unbounded storage growth as the accepted cost

#### Scenario: Status values
- **WHEN** a filtered event row exists
- **THEN** its `status` column SHALL be one of: `filtered` (connector-side filter applied), `error` (connector-side processing error, e.g. validation failure), `replay_pending` (replay requested, awaiting connector pickup), `replay_complete` (replay submitted to Switchboard successfully), `replay_failed` (replay attempted but failed)

## ADDED Requirements

### Requirement: Replay lineage and event payload age independently

Replay history and the event it describes live in two different tables with two
different retention contracts, and the system SHALL NOT assume they age out
together.

Replay-history entries SHALL be served from `public.audit_log`, which is
retained indefinitely under the Audit Log Retention requirement of
`dashboard-audit-log`. The event payload being replayed lives in
`connectors.filtered_events`, which is partitioned and has a pruner. It follows
that a replay-history entry MAY outlive the payload it refers to, and no
component MAY treat the presence of a lineage record as proof that the
underlying event row still exists.

Replay history SHALL NOT be re-sourced from `connectors.filtered_events` or any
other prunable table. Doing so would silently convert an indefinitely retained
audit record into a deletable one, which is a retention change and requires the
owner decision described below rather than a refactor.

#### Scenario: Lineage outlives a dropped payload

- **WHEN** a partition of `connectors.filtered_events` is dropped and a replay
  audit entry for one of its events still exists
- **THEN** the replay-history read still returns that entry
- **AND** the read does not fail or 500 because the underlying event row is gone
- **AND** nothing infers from the surviving entry that the event is still
  replayable

#### Scenario: Replay history is not moved onto prunable storage

- **WHEN** the replay-history read path is changed
- **THEN** it still reads `public.audit_log`
- **AND** a change that sources it from `connectors.filtered_events` is rejected
  as a retention change, not accepted as a refactor

#### Scenario: No sweep reaches the audit log

- **WHEN** any retention sweep runs
- **THEN** it does not delete, truncate, or drop anything in `public.audit_log`
- **AND** the append-only guarantee of the audit log is unaffected by ingestion
  retention

### Requirement: Enabling deletion is an owner decision

Deleting owner data SHALL require an explicit owner decision recorded as a
retention window in this specification. A configuration flag alone SHALL NOT be
sufficient authority to begin deleting.

Consequently, while no retention window is recorded here, the
`filtered_events_partition_prune` job SHALL remain unscheduled and its safety
defaults SHALL remain `enabled = false` and `dry_run = true`. A change that
schedules the job, or flips either default, SHALL be treated as enacting a
retention policy and SHALL be accompanied by the recorded window it enacts.

The absence of a recorded window is a deliberate, stated position rather than an
oversight: the storage-growth cost of keeping everything is accepted in exchange
for never irreversibly losing an event the owner did not agree to lose.

#### Scenario: Scheduling a sweep without a recorded window is rejected

- **WHEN** a change adds a schedule entry for a retention pruner
- **AND** no retention window is recorded in this specification
- **THEN** the change is rejected, and the automated guard covering roster
  configuration fails

#### Scenario: Flipping a safety default is a retention change

- **WHEN** a change sets the pruner's `enabled` default to true, or its
  `dry_run` default to false
- **THEN** the change is treated as enacting deletion and requires the recorded
  window
- **AND** the automated guard covering those defaults fails until the window
  exists

#### Scenario: Keep-forever is the stated position until decided

- **WHEN** an operator asks what the retention window for filtered events is
- **THEN** the answer is that there is none, deliberately, and nothing is deleted
- **AND** the accepted cost is unbounded growth of `connectors.filtered_events`
