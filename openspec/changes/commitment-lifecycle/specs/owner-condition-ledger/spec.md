## ADDED Requirements

### Requirement: Explicit Condition Resolution

The condition ledger engine SHALL support explicit resolution of an active
condition without requiring a producer snapshot. `resolve_condition()` SHALL
transition the active (`open`/`aging`) episode for a given `(source,
fingerprint)` to `resolved`, recording `resolved_at`, `recovered_after_s`,
and adding caller-supplied `resolution_metadata` to the row's `metadata` JSONB
without replacing existing top-level creation-time metadata values.

ID: REQ-owner-condition-ledger-004
Source: RFC 0026 §1 (Explicit Resolution Path)
Scope: v1-mandatory

#### Scenario: Resolving an active condition explicitly

- **WHEN** `resolve_condition()` is called with a `source` and `fingerprint`
  that match an active (`open` or `aging`) episode
- **THEN** the episode transitions to `resolved` with `resolved_at` set to
  the current time and `recovered_after_s` computed from `first_detected_at`
- **AND** new `resolution_metadata` keys are added to the row's `metadata` JSONB
- **AND** an existing metadata value wins if the caller supplies the same key

#### Scenario: Resolution metadata does not clobber creation-time metadata

- **WHEN** `resolve_condition()` is called with `resolution_metadata`
  containing keys like `evidence_closed` and `resolution_reason`
- **THEN** the resolution metadata is merged into the existing `metadata`
  JSONB using shallow, creation-wins top-level merge
  (`resolution_metadata || metadata`)
- **AND** every existing top-level metadata value, including `class`, `kind`,
  `direction`, `counterparty_entity_id`, `confidence`, `evidence_opened`, and
  `identity_payload`, retains its value on collision

#### Scenario: Resolving a non-existent or already-resolved condition

- **WHEN** `resolve_condition()` is called with a `source` and `fingerprint`
  that have no active episode (never existed, or already resolved)
- **THEN** the function returns `None` without modifying any row

#### Scenario: Concurrent explicit resolution and clean complete snapshot

- **WHEN** `resolve_condition()` and `reconcile_snapshot()` are called
  concurrently for the same `source`, and the complete snapshot omits the
  target fingerprint
- **THEN** exactly one succeeds atomically; the other observes the updated
  inactive state without a duplicate resolution or deadlock — both use the
  same transaction-scoped advisory lock keyed by
  `hashtext(table || ':' || source)`

#### Scenario: Re-observing an explicitly resolved identity

- **WHEN** `resolve_condition()` has resolved an episode and a later complete
  snapshot observes the same `(source, fingerprint)` identity
- **THEN** reconciliation creates the next episode rather than mutating the
  resolved row

#### Scenario: Empty complete snapshot resolves condition already resolved explicitly

- **WHEN** `resolve_condition()` resolves a condition, and subsequently
  `reconcile_snapshot(snapshot_complete=True)` is called with an empty
  observation list for the same source
- **THEN** the snapshot reconciliation finds no active episodes and produces
  no transitions — the already-resolved condition is not re-resolved or
  reopened

#### Scenario: Complete snapshot still observing a condition after explicit resolution

- **WHEN** `resolve_condition()` resolves a condition, and subsequently
  `reconcile_snapshot(snapshot_complete=True)` is called with an observation
  list that still includes the resolved condition's fingerprint
- **THEN** a new episode is opened for that fingerprint (episode N+1) because
  the producer still observes the condition — the explicit resolution closed
  episode N, and re-observation is a new occurrence per the existing
  recurrence rule

### Requirement: Explicit Resolution MCP Tool

The Switchboard butler SHALL expose a `resolve_owner_condition` MCP tool so an
LLM-driven butler session can explicitly resolve an active owner condition
while remaining MCP-only.

ID: REQ-owner-condition-ledger-005
Source: RFC 0026 §2 (MCP Tool: resolve_owner_condition)
Scope: v1-mandatory

#### Scenario: Resolving a condition via MCP

- **WHEN** a butler session calls `resolve_owner_condition` with a valid
  `source`, `fingerprint`, and `resolution_reason`
- **THEN** the tool resolves the matching active condition and returns
  `{"status": "resolved", "episode": <n>, "fingerprint": "...",
  "resolution_reason": "..."}`

#### Scenario: Resolving a condition that does not exist

- **WHEN** `resolve_owner_condition` is called with a `source` and
  `fingerprint` that have no active episode
- **THEN** it returns `{"status": "not_found"}`

#### Scenario: Invalid resolution reason is rejected

- **WHEN** `resolve_owner_condition` receives a `resolution_reason` not in
  the allowed set (`satisfied`, `cancelled`, `superseded`, `expired`)
- **THEN** it returns `{"status": "error", "reason": "..."}` without
  attempting a database write
