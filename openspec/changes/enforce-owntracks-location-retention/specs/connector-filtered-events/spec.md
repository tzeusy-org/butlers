## MODIFIED Requirements

### Requirement: Filtered Events Table

- The `connectors.filtered_events` table persists every message a connector observes but does not submit to the Switchboard — one row per filtered or errored message. Errored rows SHALL persist the full available payload for replay; filtered rows SHALL persist a bounded preview with the raw payload redacted (see the Filtered-Content Privacy Tier requirement).
- The OwnTracks source-purge branch SHALL retain the filtered/error operational row/status and existing permanent replay-audit contract while stripping only exact source-derived raw location fields after real accepted-source coverage and owning-holder receipt. An unaccepted error/unprojected raw copy SHALL remain blocked and visible rather than be ingested merely for deletion. This is a source-specific payload lifetime refinement, not activation of the disabled partition pruner, a new generic retention window or deletion of audit history. Every other connector and original initial-persistence branch SHALL remain.

ID: REQ-connector-filtered-events-002
Source: bu-s11n0s.7 original S1/S2; about/heart-and-soul/security.md Sensitive Data Categories; openspec/specs/connector-filtered-events/spec.md; proposed location-retention amendment
Scope: v1-mandatory

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

#### Scenario: Filtered Events Table preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** The OwnTracks source-purge branch SHALL retain the filtered/error operational row/status and existing permanent replay-audit contract while stripping only exact source-derived raw location fields after real accepted-source coverage and owning-holder receipt. An unaccepted error/unprojected raw copy SHALL remain blocked and visible rather than be ingested merely for deletion. This is a source-specific payload lifetime refinement, not activation of the disabled partition pruner, a new generic retention window or deletion of audit history. Every other connector and original initial-persistence branch SHALL remain.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof

### Requirement: Full Payload Shape

- The `full_payload` JSONB column SHALL store envelope metadata sufficient to reconstruct an `ingest.v1` envelope shape. For errored rows it additionally retains the raw provider payload for full-fidelity replay; for filtered rows the raw payload is redacted per the Filtered-Content Privacy Tier requirement, so replay of filtered rows is best-effort (metadata plus bounded preview only).
- After genuine covered OwnTracks source forgetting, the holder SHALL preserve envelope metadata and source-purge provenance but remove the matching raw location payload and coordinate-bearing normalized preview, rendering an explicit unavailable-for-replay descriptor. A caller-supplied retention_tombstone/control flag SHALL not establish this status: the owning stored source/tombstone relation SHALL decide it. Prior normal/error persistence and other connector full-fidelity replay SHALL remain; unknown/ambiguous linkage blocks this transition.

ID: REQ-connector-filtered-events-004
Source: bu-s11n0s.7 original S1/S2; about/heart-and-soul/security.md Sensitive Data Categories; openspec/specs/connector-filtered-events/spec.md; proposed location-retention amendment
Scope: v1-mandatory

#### Scenario: Payload contains envelope fields
- **WHEN** a filtered event is persisted
- **THEN** `full_payload` SHALL contain the keys: `source` (channel, provider, endpoint_identity), `event` (external_event_id, external_thread_id, observed_at, plus external_conversation_id and reply_target_ref when the producer has split conversation identity from reply targeting), `sender` (identity), `payload` (raw, normalized_text), and `control` (policy_tier)
- **AND** `schema_version` SHALL be omitted (always `ingest.v1` on replay)
- **AND** for rows with status `filtered`, `full_payload.payload.raw` SHALL be empty (`{}`) — the full raw provider payload is not retained

#### Scenario: Pre-split rows replay with split identity
- **WHEN** a stored row written before the conversation-identity split is replayed and its `event` lacks `external_conversation_id`
- **THEN** replay SHALL derive the key live ingress emits today: for `telegram_bot`, from the retained Telegram update (keeping a forum topic), else from the chat prefix of `external_thread_id`; for `telegram_user_client`, `telegram:<external_thread_id>`; for `whatsapp_user_client`, `whatsapp:<external_thread_id>`
- **AND** this adapter is bounded by filtered-event retention and SHALL be removed once no pre-split row can remain

#### Scenario: Payload for error status
- **WHEN** a message fails with status `error`
- **THEN** `full_payload` SHALL contain whatever envelope fields were available at the point of failure
- **AND** incomplete payloads are acceptable — replay of error-status events MAY fail again if the root cause is not fixed

#### Scenario: Full Payload Shape preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** After genuine covered OwnTracks source forgetting, the holder SHALL preserve envelope metadata and source-purge provenance but remove the matching raw location payload and coordinate-bearing normalized preview, rendering an explicit unavailable-for-replay descriptor. A caller-supplied retention_tombstone/control flag SHALL not establish this status: the owning stored source/tombstone relation SHALL decide it. Prior normal/error persistence and other connector full-fidelity replay SHALL remain; unknown/ambiguous linkage blocks this transition.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof

### Requirement: Filtered-Content Privacy Tier

- Content that a connector deliberately declines to submit to the Switchboard SHALL be persisted under a minimal-retention privacy tier: the connector chose not to process it, so its full raw payload MUST NOT be retained. This tier applies to every `filtered`-status row regardless of connector or filter reason. Errored rows (`error` status) are exempt — a processing failure is not a discretion decision, and its payload is retained for diagnosis and replay.
- This resolves a real divergence between connectors: the WhatsApp user-client persisted filtered content as an empty raw payload plus a bounded preview, while several other connectors (Telegram user-client, Gmail, Discord, Google Calendar, Google Health, Spotify, Telegram bot) persisted the full raw provider payload for the same class of dropped content. The minimal-retention posture is now normative.
- The existing filtered raw={} bounded-preview rule SHALL remain for all connectors. The OwnTracks accepted-and-covered source-purge floor additionally applies to any matching errored/replay raw copy and precision-bearing preview, only on genuine owning linkage/receipt. Initial unaccepted error persistence stays visible/unprojected and cannot be treated as projected or erased by a client flag. No unrelated provider privacy tier SHALL change.

ID: REQ-connector-filtered-events-005
Source: bu-s11n0s.7 original S1/S2; about/heart-and-soul/security.md Sensitive Data Categories; openspec/specs/connector-filtered-events/spec.md; proposed location-retention amendment
Scope: v1-mandatory

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

#### Scenario: Filtered-Content Privacy Tier preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** The existing filtered raw={} bounded-preview rule SHALL remain for all connectors. The OwnTracks accepted-and-covered source-purge floor additionally applies to any matching errored/replay raw copy and precision-bearing preview, only on genuine owning linkage/receipt. Initial unaccepted error persistence stays visible/unprojected and cannot be treated as projected or erased by a client flag. No unrelated provider privacy tier SHALL change.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof

### Requirement: Replay lineage and event payload age independently

- Replay history and the event it describes live in two different tables with two different retention contracts, and the system SHALL NOT assume they age out together.
- Replay-history entries SHALL be served from `public.audit_log`, which is retained indefinitely under the Audit Log Retention requirement of `dashboard-audit-log`. The event payload being replayed lives in `connectors.filtered_events`, which is partitioned and has a pruner. It follows that a replay-history entry MAY outlive the payload it refers to, and no component MAY treat the presence of a lineage record as proof that the underlying event row still exists.
- Replay history SHALL NOT be re-sourced from `connectors.filtered_events` or any other prunable table. Doing so would silently convert an indefinitely retained audit record into a deletable one, which is a retention change and requires the owner decision described below rather than a refactor.
- An OwnTracks source-purge payload tombstone SHALL make that exact raw replay unavailable while public.audit_log lineage continues to be served indefinitely. Neither permanent audit existence nor replay_complete status proves raw payload survives. No audit sweep or move to prunable history storage occurs. Widening/source reset cannot refill that raw source, while a genuinely new admitted observation retains its separate lineage and ordinary replay behavior.

ID: REQ-connector-filtered-events-006
Source: bu-s11n0s.7 original S1/S2; about/heart-and-soul/security.md Sensitive Data Categories; openspec/specs/connector-filtered-events/spec.md; proposed location-retention amendment
Scope: v1-mandatory

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

#### Scenario: Replay lineage and event payload age independently preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** An OwnTracks source-purge payload tombstone SHALL make that exact raw replay unavailable while public.audit_log lineage continues to be served indefinitely. Neither permanent audit existence nor replay_complete status proves raw payload survives. No audit sweep or move to prunable history storage occurs. Widening/source reset cannot refill that raw source, while a genuinely new admitted observation retains its separate lineage and ordinary replay behavior.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof
