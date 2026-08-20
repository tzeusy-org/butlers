## ADDED Requirements

### Requirement: Explicit Condition Resolution

The condition ledger engine SHALL support explicit resolution of an active
condition without requiring a producer snapshot. `resolve_condition()` SHALL
transition the active (`open`/`aging`) episode for a given `(source,
fingerprint)` to `resolved`, recording `resolved_at`, `recovered_after_s`,
and merging caller-supplied `resolution_metadata` into the row's `metadata`
JSONB without replacing creation-time metadata.

ID: REQ-owner-condition-ledger-004
Source: RFC 0026 §1 (Explicit Resolution Path)
Scope: v1-mandatory

#### Scenario: Resolving an active condition explicitly

- **WHEN** `resolve_condition()` is called with a `source` and `fingerprint`
  that match an active (`open` or `aging`) episode
- **THEN** the episode transitions to `resolved` with `resolved_at` set to
  the current time and `recovered_after_s` computed from `first_detected_at`
- **AND** `resolution_metadata` is merged into the row's `metadata` JSONB

#### Scenario: Resolving a non-existent or already-resolved condition

- **WHEN** `resolve_condition()` is called with a `source` and `fingerprint`
  that have no active episode (never existed, or already resolved)
- **THEN** the function returns `None` without modifying any row

#### Scenario: Concurrent resolution and snapshot reconciliation

- **WHEN** `resolve_condition()` and `reconcile_snapshot()` are called
  concurrently for the same `source`
- **THEN** exactly one succeeds atomically; the other observes the updated
  state — both use the same transaction-scoped advisory lock keyed by
  `hashtext(table || ':' || source)`

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
