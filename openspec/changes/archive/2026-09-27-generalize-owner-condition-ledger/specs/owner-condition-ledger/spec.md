## ADDED Requirements

### Requirement: Owner Condition Ledger Representation

The system SHALL maintain `public.owner_conditions`, a durable append-per-
episode ledger for owner-facing standing concerns, sharing its exact
reconciliation engine and lifecycle semantics with `infra_conditions`
(the `infrastructure-reliability` capability) via `butlers.core.
condition_ledger`. At most one active (`open`/`aging`) episode SHALL exist
per `(source, fingerprint)` identity.

#### Scenario: Reconciling a new observation opens an episode

- **WHEN** `owner_conditions.reconcile_snapshot` is called with an
  observation whose fingerprint has no active episode for its `source`
- **THEN** a new episode row is inserted at state `open`, escalation level
  `L0`, due for `L1` after the given grace period
- **AND** the transition is `opened` for that identity's first-ever episode,
  or `reopened` if a prior episode for the same identity already resolved

#### Scenario: A confirmed observation past its due date escalates

- **WHEN** `reconcile_snapshot` confirms an active episode whose
  `next_reescalate_at` has passed
- **THEN** the episode's escalation level advances and its state becomes
  `aging`, atomically with the confirmation, returning transition
  `escalation_due`

#### Scenario: A complete snapshot resolves what it no longer observes

- **WHEN** `reconcile_snapshot` is called with `snapshot_complete=True` and
  an active episode for `source` is absent from the observations
- **THEN** that episode transitions to `resolved`, with `resolved_at` and
  `recovered_after_s` set
- **AND** a `snapshot_complete=False` call never resolves any episode by
  omission, though it still confirms evidence for what it did observe

#### Scenario: Recurrence preserves resolved history

- **WHEN** an identity that previously resolved is observed again
- **THEN** a new episode row is inserted with the next episode number for
  that identity, and the previously resolved row is never mutated

### Requirement: Owner Condition Ledger MCP Surface

The Switchboard butler SHALL expose a `reconcile_owner_condition` MCP tool
so an LLM-driven butler session can reconcile a standing owner-facing
concern while remaining MCP-only, consistent with the schema-isolation
model. A deterministic scheduled job with its own database pool SHALL call
`butlers.core.owner_conditions.reconcile_snapshot` directly and in-process
instead, mirroring the existing split between `propose_insight_candidate`'s
MCP tool and its direct-import path.

#### Scenario: An LLM-driven session reconciles a condition via MCP

- **WHEN** a butler session calls `reconcile_owner_condition` with a
  `source`, a list of observations, and `snapshot_complete`
- **THEN** the tool reconciles `public.owner_conditions` and returns
  `{"status": "accepted", "transitions": [...]}`, each transition carrying
  its fingerprint, episode, state, transition kind, and escalation level

#### Scenario: Invalid input is rejected before touching the pool

- **WHEN** `reconcile_owner_condition` receives an observation missing
  `fingerprint`, or an empty `source`
- **THEN** it returns `{"status": "error", "reason": "..."}` without
  attempting a database write

### Requirement: Finance Butler Reconciles Standing Concerns

The Finance butler's `insight-scan` scheduled job SHALL reconcile two
categories into the owner condition ledger, in-process and best-effort,
alongside (not instead of) its existing cooldown-gated insight-candidate
submission for those categories: overdue bills (`source=
"finance:bill-overdue"`, fingerprint keyed on the bill) and monthly spending
anomalies (`source="finance:spending-anomaly"`, fingerprint keyed on
category and calendar month).

#### Scenario: A bill past its due date and still pending opens a condition

- **WHEN** `run_insight_scan` observes a `finance.bills` row with
  `status='pending'` and `due_date` before today
- **THEN** it reconciles an owner condition for `finance:bill-overdue`
  identified by the bill, in addition to any existing insight-candidate
  behavior for that bill

#### Scenario: Paying an overdue bill resolves its condition

- **WHEN** a bill's status changes away from `pending` (e.g. to `paid`)
- **THEN** the next `run_insight_scan` run's complete overdue-bill snapshot
  no longer observes it, and its owner condition resolves

#### Scenario: A reconciliation failure never breaks the insight scan

- **WHEN** the owner condition ledger reconciliation raises (e.g. the table
  is unreachable or mid-migration)
- **THEN** `run_insight_scan` logs a warning and continues; insight-candidate
  submission for that run is unaffected
