# deployment-and-drift

## MODIFIED Requirements

### Requirement: Three-Way Migration Drift Comparison

The system SHALL compare, per butler schema, the codebase's current Alembic
head revision for every migration chain applicable to that schema against the
revision(s) actually present in that schema's `alembic_version` table.

#### Scenario: A schema's chains are resolved the same way the migration runner resolves them

- **WHEN** the comparison determines which migration chains apply to a given
  butler schema
- **THEN** it includes the `core` chain always, the butler's own chain if one
  exists, and every enabled module chain that has migrations of its own
- **AND** this resolution mirrors exactly what `butlers.cli._migrate_all`
  applies at boot — the comparison and the runner never disagree about which
  chains apply to a schema

#### Scenario: All chains aligned reports no drift

- **WHEN** every applicable chain's codebase head revision is present in a
  schema's `alembic_version` table
- **THEN** the comparison reports no drift for that schema

#### Scenario: A dark revision is detected

- **WHEN** a chain's codebase head revision is NOT present among a schema's
  applied revisions for that chain (the bu-zhfd0 shape: a merged revision
  that was never actually deployed)
- **THEN** the comparison reports drift for that `(schema, chain)` pair,
  including the expected head and the schema's actual applied revision for
  that chain (or `null` if the chain has never been applied to that schema
  at all)

#### Scenario: A chain that was never applied is distinguished from a stale one

- **WHEN** a schema's `alembic_version` table has no row belonging to a given
  chain's revision-id namespace (e.g. no `core_*` row at all)
- **THEN** the drift entry's actual revision is reported as `null`, distinct
  from a stale-but-present revision

#### Scenario: A check failure is an incomplete snapshot, never a false all-clear

- **WHEN** the comparison itself cannot complete (the shared database pool is
  unavailable, or reading a schema's `alembic_version` table raises an
  unexpected error)
- **THEN** the result is marked degraded/unavailable rather than reporting
  "no drift" or raising to the caller
- **AND** it is treated as an incomplete infrastructure-condition snapshot
  that cannot resolve any active migration-drift condition
- **AND** a schema whose `alembic_version` table does not exist at all (a
  schema that predates any migration run) is treated as a legitimate empty
  state, not a check failure — every applicable chain is reported as never
  applied for that schema

#### Scenario: A check failure is a degraded result, never a false all-clear or a crash

- **WHEN** the comparison itself cannot complete (the shared database pool is
  unavailable, or reading a schema's `alembic_version` table raises an
  unexpected error)
- **THEN** the result is marked degraded/unavailable rather than reporting
  "no drift" or raising to the caller
- **AND** a schema whose `alembic_version` table does not exist at all (a
  schema that predates any migration run) is treated as a legitimate empty
  state, not a check failure — every applicable chain is reported as never
  applied for that schema

### Requirement: Hourly Sentinel Cadence

The system SHALL re-run the three-way comparison on an hourly cadence as a
background process, independent of any single dashboard page view.

#### Scenario: Sentinel loop never crashes its host process

- **WHEN** a single comparison tick fails for any reason
- **THEN** the failure is logged and the loop continues to the next tick —
  one bad tick never terminates the background loop or the process hosting
  it

#### Scenario: The sentinel loop reconciles lifecycle decisions, not cached reads

- **WHEN** `GET /api/system/drift` (see below) is called
- **THEN** it computes the comparison live on that request rather than
  reading a value cached by the hourly loop — the endpoint is always at
  least as fresh as the loop
- **AND** the hourly loop's distinct responsibility is submitting complete
  drift snapshots to infrastructure-condition reconciliation and processing
  any resulting due lifecycle transitions
- **AND** it does not treat audit-marker absence or a degraded report as
  resolution authority

#### Scenario: The sentinel loop's job is the escalation side effect, not serving reads

- **WHEN** `GET /api/system/drift` (see below) is called
- **THEN** it computes the comparison live on that request rather than
  reading a value cached by the hourly loop — the endpoint is always at
  least as fresh as the loop
- **AND** the hourly loop's distinct responsibility is persisting the
  first-detected/escalated debounce state and triggering escalation once the
  24h threshold is crossed (see below)

### Requirement: GET /api/system/drift

The `/api/system/drift` endpoint SHALL return the current three-way
comparison result plus active-episode escalation state, following the
fleet-wide degraded-envelope convention (never a fabricated all-clear, never
an unhandled 503 for a known degraded state).

#### Scenario: Endpoint returns the comparison result

- **WHEN** `GET /api/system/drift` is called
- **THEN** the response body contains:
  - `checked_at: string` -- ISO 8601 UTC timestamp of this comparison
  - `is_drifted: boolean`
  - `drifted: DriftEntry[]` -- one entry per `(schema, chain)` pair out of
    sync, each with `schema_name`, `chain`, `expected_head`, and
    `actual_revision: string | null`
  - `first_detected_at: string | null` -- when the current drift episode was
    first detected, or `null` if not drifted
  - `escalated: boolean` -- whether the current active episode has emitted L1
    or higher; it SHALL NOT mean a permanent already-escalated latch
  - `drift_check_available: boolean`
- **AND** the response wraps in the standard `ApiResponse<DriftFacts>`
  envelope

#### Scenario: A degraded check is never rendered as clean

- **WHEN** the comparison itself fails (per the check-failure scenario above)
- **THEN** the endpoint returns HTTP 200 with `drift_check_available: false`
  and `is_drifted: false`, `drifted: []`, `first_detected_at: null`,
  `escalated: false` -- a caller MUST treat `drift_check_available: false` as
  "unknown," never as "clean"

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

#### Scenario: First sighting does not escalate

- **WHEN** a drift composition (the specific set of drifted schema/chain
  pairs) is detected for the first time
- **THEN** a first-detected marker is persisted (keyed by a stable
  fingerprint of that composition) and no escalation occurs yet

#### Scenario: Drift within the 24h threshold does not escalate

- **WHEN** the same drift composition has been continuously detected for
  less than 24 hours since its first-detected marker
- **THEN** no escalation occurs on that tick

#### Scenario: Drift past the 24h threshold escalates exactly once

- **WHEN** the same drift composition has persisted for more than 24 hours
  and has not already been escalated
- **THEN** a QA-visible case is opened via the existing self-healing
  case-tracking primitives (`public.healing_attempts`), created and
  immediately transitioned to the terminal `unfixable` status with an
  `error_detail` carrying a human-action marker -- the same convention the
  QA dossier already uses to classify "needs a human, not a code fix" cases
  (distinct from an `investigating` case that could trigger an unwanted
  healing-agent PR attempt)
- **AND** an escalated marker is persisted for that composition so
  subsequent ticks do not re-escalate it
- **AND** a partial resolution that changes the drift composition (a new
  fingerprint) resets the first-detected clock for the new composition —
  this is an accepted simplification, not a continuously-tracked single
  episode

#### Scenario: An escalation attempt failure degrades, does not crash

- **WHEN** writing the escalation case fails (database error, etc.)
- **THEN** the failure is logged and reported in the tick's summary; the
  sentinel loop continues to its next tick rather than crashing

## Source References
- Non-Negotiable Rule 4 (deterministic daemon infrastructure)
- RFC 0001 (daemon lifecycle and deterministic background work)
- RFC 0005 (recovery telemetry and decision/execution separation)
- `infrastructure-reliability` (episode, snapshot, and escalation contract)
