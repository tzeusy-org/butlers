## ADDED Requirements

### Requirement: Wake-Recovery Hold Provenance
The originating `notify()` path SHALL attach immutable wake-recovery admission
provenance to every newly persisted routine owner-default hold created by the
Owner Attention Policy quiet-hours decision. The provenance SHALL include hold
kind `owner_attention_quiet_hours`, canonical policy-window key, origin-local
admission sequence, original stored `deliver_at`, resolved-envelope digest, and
the fully resolved Telegram endpoint, bot/chat/thread target tuple. It SHALL
retain the full resolved `notify.v1` envelope in the origin schema and SHALL
NOT copy notification message content into a shared coordination record.

The provenance SHALL be written atomically with the deferred row and SHALL NOT
be inferred from timestamps, attention-ledger rows, context-only holds, or a
later target lookup. Rows created before this provenance exists, explicitly
targeted rows, high-priority rows, retry rows, and context-only holds SHALL NOT
be represented as wake-recovery-eligible merely because they are pending.

#### Scenario: New policy hold retains exact provenance
- **WHEN** an eligible routine implicit-owner notification is durably held by
  the Owner Attention Policy quiet-hours gate
- **THEN** the origin persists the resolved envelope and all required
  wake-recovery provenance in the same durable admission
- **AND** a later prepare operation can return only metadata and content needed
  for deterministic composition without granting shared queue access

#### Scenario: Legacy and non-policy rows remain in their normal path
- **WHEN** a deferred row lacks wake-recovery provenance or was held for
  explicit targeting, high priority, retry, or active context only
- **THEN** wake recovery excludes the row
- **AND** the existing deferred-notification scheduler retains its established
  behavior for that row
