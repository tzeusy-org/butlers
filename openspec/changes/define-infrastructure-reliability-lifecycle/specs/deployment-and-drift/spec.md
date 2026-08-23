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

## Source References
- Non-Negotiable Rule 4 (deterministic daemon infrastructure)
- RFC 0001 (daemon lifecycle and deterministic background work)
- RFC 0005 (recovery telemetry and decision/execution separation)
- `infrastructure-reliability` (episode, snapshot, and escalation contract)
