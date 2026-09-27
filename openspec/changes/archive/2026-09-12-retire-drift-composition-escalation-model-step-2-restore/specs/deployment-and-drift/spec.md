## ADDED Requirements

### Requirement: QA Escalation After Sustained Drift

The system SHALL reconcile each affected migration `(schema, chain)` pair as a
`deployment_drift` infrastructure-condition episode. Its fingerprint SHALL use
the versioned sorted stable schema/chain identity, while expected revision,
actual revision, timestamps, and diagnostic prose remain evidence. The system
SHALL replace first-detected/already-escalated one-shot semantics with the
infrastructure-reliability lifecycle schedule.

#### Scenario: First sighting opens L0 evidence

- **WHEN** a drifted `(schema, chain)` pair is detected for the first time
- **THEN** infrastructure-condition reconciliation creates an L0 `open`
  episode for that pair and no escalation occurs yet
- **AND** it does not use a composition-wide audit marker as current-state
  authority

#### Scenario: Drift within the source-owned grace does not escalate

- **WHEN** the same active drift episode has persisted for less than the
  deployment-drift L1 grace of 24 hours
- **THEN** no escalation occurs on that tick
- **AND** the episode remains active with its evidence refreshed

#### Scenario: Drift at L1 preserves the terminal human-action shape once per episode

- **WHEN** the same drift episode reaches L1 and its L1 transition is due
- **THEN** a QA-visible case is opened via the existing self-healing
  case-tracking primitives (`public.healing_attempts`), created and
  immediately transitioned to the terminal `unfixable` status with an
  `error_detail` carrying a human-action marker -- the same convention the
  QA dossier already uses to classify "needs a human, not a code fix" cases
  (distinct from an `investigating` case that could trigger an unwanted
  healing-agent PR attempt)
- **AND** that L1 side effect is emitted at most once for the episode

#### Scenario: Continuing drift re-escalates without additional healing attempts

- **WHEN** an active drift episode reaches L2, L3, or a seven-day L3 repeat
- **THEN** the sentinel records a distinct re-escalation audit event for the
  due lifecycle transition
- **AND** it does not create another `healing_attempt` for that episode
- **AND** concurrent ticks do not duplicate the same due action

#### Scenario: Complete recovery resolves and later recurrence starts anew

- **WHEN** a complete successful drift comparison no longer observes an
  active `(schema, chain)` condition
- **THEN** the episode resolves once and records recovery evidence
- **AND** a later recurrence creates a new L0 episode with a new L1 grace
  period rather than reusing the resolved episode's escalation state

#### Scenario: An escalation consequence failure degrades, does not crash

- **WHEN** writing a due escalation consequence fails (database error, etc.)
- **THEN** the failure is logged and reported in the tick's summary
- **AND** the sentinel loop continues to its next tick rather than crashing
