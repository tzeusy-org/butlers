## Tasks

### 1. Extend condition_ledger with resolve_condition()

Add `resolve_condition()` to `butlers.core.condition_ledger` per RFC 0026 §1.
Re-export via `butlers.core.owner_conditions`. Unit tests for: resolve active,
reject double-resolve, concurrency with reconcile_snapshot.

Acceptance:
- REQ-owner-condition-ledger-004 scenarios pass
- Advisory lock concurrency verified under parallel test execution

### 2. Add resolve_owner_condition MCP tool

Extend `roster/switchboard/modules/owner_conditions_broker.py` with
`resolve_owner_condition` tool. Input validation for resolution_reason enum.

Acceptance:
- REQ-owner-condition-ledger-005 scenarios pass
- MCP integration test: resolve from session, reject invalid reason, handle
  not-found

### 3. Commitment helper module

Create `src/butlers/core/commitments.py` with `create_commitment()`,
`resolve_commitment()`, `list_active_commitments()`,
`list_entity_commitments()`. Metadata validation, fingerprint computation,
confidence threshold enforcement.

Acceptance:
- REQ-commitment-lifecycle-001 through 004 scenarios pass
- Validation rejects invalid metadata, low confidence, missing evidence
- Fingerprint identity is stable and deterministic

### 4. Commitment escalation job

Create `src/butlers/jobs/commitment_escalation.py`. Queries commitment-class
owner_conditions at L1+ with confidence >= 0.8, proposes insight candidates.
Deadline-aware grace period shortening. 90-day garbage collection.

Acceptance:
- REQ-commitment-lifecycle-005 and 006 scenarios pass
- Composes with insight engine without modifying it
- Deadline-bearing commitments surface before deadline

### 5. Relationship Butler commitment extraction

Extend signal extraction and Relationship session skills to detect explicit
first-person commitment patterns, create commitment-class owner_conditions,
and resolve from conversational evidence.

Acceptance:
- REQ-commitment-lifecycle-007 and 008 scenarios pass
- False-positive rate < 20% on curated 10-statement test set
- Resolution provenance recorded on every resolved commitment

### 6. Dashboard commitment panel

Extend StandingConditionsTile to filter and render commitment-class conditions
with counterparty name, deadline, escalation level, and direction indicator.

Acceptance:
- Commitment-class conditions render with structured metadata
- Non-commitment conditions continue rendering unchanged
