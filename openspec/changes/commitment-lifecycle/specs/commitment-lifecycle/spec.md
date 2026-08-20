## ADDED Requirements

### Requirement: Commitment Metadata Convention

The system SHALL recognize a commitment as an `owner_conditions` row where
`metadata->>'class' = 'commitment'`. The `metadata` JSONB SHALL carry
structured commitment fields: `kind` (promise, waiting_for, follow_up,
obligation, decision), `direction` (owner_to_other, other_to_owner, self),
`counterparty_entity_id` (nullable UUID referencing `public.entities`),
`confidence` (0.0–1.0), `evidence_opened` (provenance of creation), and
`evidence_closed` (provenance of resolution, populated on resolution).

ID: REQ-commitment-lifecycle-001
Source: RFC 0026 §3 (Commitment Metadata Convention)
Scope: v1-mandatory

#### Scenario: Creating a commitment stores structured metadata

- **WHEN** a domain butler creates a commitment via `reconcile_snapshot` with
  `snapshot_complete=False` and metadata containing `"class": "commitment"`
- **THEN** the `owner_conditions` row is created with the full commitment
  metadata schema, including `kind`, `direction`, `confidence`, and
  `evidence_opened`

#### Scenario: Resolving a commitment preserves opening evidence

- **WHEN** a commitment is resolved via `resolve_condition` with
  `resolution_metadata` containing `evidence_closed` and `resolution_reason`
- **THEN** the resolution metadata is merged into the row's existing metadata
  without replacing `evidence_opened` or other creation-time fields

#### Scenario: Querying commitments by counterparty

- **WHEN** a caller queries `owner_conditions` filtering by
  `metadata->>'class' = 'commitment'` and
  `metadata->>'counterparty_entity_id' = '<entity-uuid>'`
- **THEN** only commitment-class conditions anchored to that entity are
  returned, regardless of originating domain butler

### Requirement: Commitment Helper Module

The system SHALL provide `butlers.core.commitments` with convenience functions
for commitment lifecycle operations: `create_commitment()` (validates metadata,
computes fingerprint, delegates to `reconcile_snapshot`),
`resolve_commitment()` (validates resolution_reason, delegates to
`resolve_condition`), `list_active_commitments()` (queries commitment-class
active conditions), and `list_entity_commitments(entity_id)` (queries by
counterparty).

ID: REQ-commitment-lifecycle-002
Source: RFC 0026 §3 (Commitment Metadata Convention)
Scope: v1-mandatory

#### Scenario: Creating a commitment with invalid metadata

- **WHEN** `create_commitment()` is called with missing required fields
  (`kind`, `direction`, or `evidence_opened`) or an invalid `kind` value
- **THEN** it raises a validation error without touching the database

#### Scenario: Creating a duplicate commitment for the same action and person

- **WHEN** `create_commitment()` is called with the same counterparty and
  action hash as an existing active commitment
- **THEN** the existing commitment is confirmed (not duplicated), and its
  `last_confirmed_at` is updated

### Requirement: Commitment Fingerprint Identity

A commitment's fingerprint SHALL be computed from stable identity facts that
define "same commitment": the counterparty entity ID and a normalized hash of
the action description. Mutable fields (deadline, confidence, summary text)
SHALL NOT be part of the fingerprint so they may change during the episode
without creating a new commitment.

ID: REQ-commitment-lifecycle-003
Source: RFC 0026 §4 (Fingerprint Identity)
Scope: v1-mandatory

#### Scenario: Same action to same person produces same fingerprint

- **WHEN** two commitment creation attempts use the same counterparty entity
  and equivalent action descriptions (modulo whitespace/case normalization)
- **THEN** they produce the same fingerprint and the second confirms the
  existing episode

#### Scenario: Different actions to same person produce different fingerprints

- **WHEN** two commitment creation attempts use the same counterparty entity
  but different action descriptions
- **THEN** they produce different fingerprints and coexist as separate
  active episodes under the same source

### Requirement: Commitment Confidence Threshold

Commitments with `confidence < 0.6` SHALL NOT be created as durable records.
Commitments with `0.6 <= confidence < 0.8` SHALL be created but never surfaced
proactively — they are available for queries (prep cards, dashboard) only.
Commitments with `confidence >= 0.8` SHALL be surfaced proactively through the
insight engine at escalation L1 and above.

ID: REQ-commitment-lifecycle-004
Source: RFC 0026 §8 (Confidence and Creation Thresholds)
Scope: v1-mandatory

#### Scenario: Low-confidence commitment is not created

- **WHEN** a domain butler attempts to create a commitment with
  `confidence = 0.5`
- **THEN** `create_commitment()` rejects the creation

#### Scenario: Medium-confidence commitment is created but not surfaced

- **WHEN** a commitment exists with `confidence = 0.7` and reaches L1
  escalation
- **THEN** the escalation job does NOT propose an insight candidate for it
- **AND** the commitment is visible on the dashboard and in prep card queries

#### Scenario: High-confidence commitment is surfaced at L1

- **WHEN** a commitment exists with `confidence = 0.9` and reaches L1
  escalation
- **THEN** the escalation job proposes an insight candidate for delivery
  through the insight engine

### Requirement: Commitment Escalation Job

A scheduled job SHALL check commitment-class `owner_conditions` for escalation
eligibility and propose insight candidates for commitments at L1 or above that
meet the surfacing confidence threshold. The job SHALL compose with the
existing insight engine (`propose_insight_candidate`) and attention ledger —
commitments compete for the same delivery budget as other insights.

ID: REQ-commitment-lifecycle-005
Source: RFC 0026 §7 (Insight Engine Integration)
Scope: v1-mandatory

#### Scenario: Deadline-bearing commitment surfaces before deadline

- **WHEN** a commitment has a deadline within its L0 grace period
- **THEN** the L0 grace period is shortened so the commitment is surfaced
  at L1 before the deadline passes

#### Scenario: Escalation proposes an insight candidate

- **WHEN** the escalation job finds a commitment at L1+ with
  `confidence >= 0.8`
- **THEN** it calls `propose_insight_candidate` with a commitment-specific
  summary, the commitment's escalation level mapped to insight priority, and
  a dedup key derived from the commitment's fingerprint

#### Scenario: Delivered insight is recorded in attention ledger

- **WHEN** a commitment insight candidate is delivered to the owner
- **THEN** the delivery is recorded in the attention ledger with source
  `"commitment"` and the commitment's fingerprint as the reference

### Requirement: Commitment Garbage Collection

Commitments at L3 escalation for 90 or more consecutive days without
re-confirmation SHALL trigger an archival insight proposing cancellation or
renewal. Cancellation resolves with `resolution_reason: "cancelled"`. Renewal
resets escalation to L1 for another cycle.

ID: REQ-commitment-lifecycle-006
Source: RFC 0026 §9 (Garbage Collection)
Scope: v1-mandatory

#### Scenario: Stale commitment proposes archival

- **WHEN** a commitment has been at L3 for 90+ days and has not been
  re-confirmed
- **THEN** the escalation job proposes an insight: "This commitment has been
  open for [N] days with no activity. Cancel or keep?"

#### Scenario: Owner cancels stale commitment

- **WHEN** the owner responds to an archival proposal by cancelling
- **THEN** the commitment resolves with `resolution_reason: "cancelled"` and
  `evidence_closed.source: "owner_confirmed"`

### Requirement: Relationship Butler Commitment Extraction

The Relationship Butler SHALL detect explicit first-person commitment patterns
in routed conversations (e.g., "I'll send", "I promised", "I need to follow
up") and create commitment-class owner conditions with the appropriate
counterparty entity, direction, and confidence. Only explicit statements with
confidence >= 0.8 SHALL be auto-created.

ID: REQ-commitment-lifecycle-007
Source: heart-and-soul/vision.md (mental labor absorption)
Scope: v1-mandatory

#### Scenario: Explicit promise detected and committed

- **WHEN** the owner says "I'll send Sam that book tomorrow" in a routed
  conversation with a resolved entity for "Sam"
- **THEN** the Relationship Butler creates a commitment with `kind: "promise"`,
  `direction: "owner_to_other"`, `counterparty_entity_id: <sam-uuid>`,
  `confidence: 0.9`, and `deadline: <tomorrow>`

#### Scenario: Ambiguous statement does not create a commitment

- **WHEN** the owner says "I should probably get around to calling Sam" in a
  routed conversation
- **THEN** no commitment is created (confidence below 0.8 for a conditional
  hedging statement)

#### Scenario: Owner resolves a commitment via conversation

- **WHEN** the owner says "I sent Sam the book" and an active commitment
  matching that action and counterparty exists
- **THEN** the Relationship Butler calls `resolve_owner_condition` with
  `resolution_reason: "satisfied"`

### Requirement: Resolution Provenance

Every commitment resolution SHALL record `evidence_closed` in the row's
metadata, containing the resolution source (owner_confirmed, evidence_observed,
expired, cancelled, superseded), the session ID if resolved from a session,
and a detail string. No silent resolution — a commitment SHALL NOT transition
to `resolved` without `evidence_closed` being populated.

ID: REQ-commitment-lifecycle-008
Source: RFC 0026 §3, JARVIS Run 08 "Accepted is not completed"
Scope: v1-mandatory

#### Scenario: Resolution without evidence is rejected

- **WHEN** `resolve_commitment()` is called without `evidence_closed`
  containing at least a `source` field
- **THEN** it raises a validation error without touching the database

#### Scenario: Resolved commitment retains opening and closing evidence

- **WHEN** a resolved commitment is queried
- **THEN** the row's metadata contains both `evidence_opened` (creation
  provenance) and `evidence_closed` (resolution provenance) with session IDs
  and timestamps
