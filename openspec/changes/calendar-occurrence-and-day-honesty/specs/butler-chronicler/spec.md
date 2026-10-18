## ADDED Requirements

### Requirement: Settled-Day Calendar Reconciliation Run

The Chronicler SHALL own an additive, scheduled, idempotent reconciliation run for settled historical days. For each settled local day (strictly before the owner's current local date and past the settling delay) it SHALL read the approved migration-tracked calendar read surface under `butler_chronicler_rw` and its own evidence layers, and SHALL persist one run record per `(local_day, algorithm_version)` in Chronicler-owned storage. The run SHALL report only deterministic outcomes per scheduled calendar block: `contradicted` (an independent rest-lane or conflicting-activity evidence contradicts the block, the same rule `reconcile_day()` already applies), or `unknown` (no evidence either way). It SHALL record counts, `computed_at`, and a `coverage` of `complete`, `partial` or `unavailable` that reflects the freshness and completeness of the calendar source and of the evidence layers it consulted.

The run SHALL NOT label any block as attended, SHALL NOT count a calendar block as lived time (the "Calendar Scheduled Blocks Are Not Attendance Assertions" requirement is unchanged), SHALL NOT overwrite or hide an owner correction in the correction overlay, and SHALL NOT mutate any non-Chronicler schema: Calendar gains no cross-schema write authority, the grant surface is the existing restricted calendar read surface (no cursors, no arbitrary schemas, no provider access), and no provider or runtime action is introduced. A rerun SHALL replace only its own run record for the same key, never an owner correction, and a replay SHALL be idempotent. A day whose calendar source was stale or failed when evaluated SHALL record `coverage = "partial"` or `"unavailable"` and SHALL be re-evaluated on a later run; it SHALL NOT be recorded as a clean run. Block references SHALL use the stable `calendar:{origin_instance_ref}` source reference so a moved or cancelled occurrence maps to the same block.

ID: REQ-butler-chronicler-008
Source: bu-h40h2b.6 acceptance 2 and 6; bu-vpn5ij; decision A1 (bu-2o1dn8); Calendar Scheduled Blocks Are Not Attendance Assertions
Scope: v1-mandatory

#### Scenario: Run reports only contradictions and unknowns

- **WHEN** the run evaluates a settled day with blocks contradicted by rest-lane evidence and blocks with no evidence
- **THEN** it records a contradicted count and an unknown count
- **AND** it records no attended count and applies no attended label to any block

#### Scenario: Owner correction is never overwritten

- **WHEN** the owner has corrected the outcome of a block and the run is recomputed
- **THEN** the correction overlay is unchanged and still takes precedence on read

#### Scenario: Replay is idempotent

- **WHEN** the same settled day is evaluated twice with unchanged inputs
- **THEN** one run record exists for `(local_day, algorithm_version)` with identical counts

#### Scenario: Stale calendar source records partial coverage

- **WHEN** the calendar source was stale or failed at evaluation time
- **THEN** the run records `coverage = "partial"` or `"unavailable"`
- **AND** a later run re-evaluates the day when the source is fresh

#### Scenario: Current and future days are never evaluated

- **WHEN** the scheduler runs for a local day that is today or later
- **THEN** no run record is written

#### Scenario: Calendar has no write path

- **WHEN** the run executes
- **THEN** it writes only Chronicler-owned storage and no `calendar_*` table is mutated

### Requirement: Reconciliation Run Read Contract

The Chronicler API SHALL expose a read-only, additive endpoint for a settled day's reconciliation run, returning `local_day`, `contradicted_count`, `unknown_count`, `computed_at`, `coverage`, and `algorithm_version`, or an explicit not-found for a day with no run. It SHALL return a not-found, never a zero-count run, for a current, future or never-evaluated day. The response SHALL NOT include an attended count or any field that states attendance. The dashboard consumes this contract; it SHALL NOT compute the result itself. Freshness semantics: a run is `stale` to its consumer when `computed_at` is older than the latest change to the calendar source or the evidence layers for that day, and the consumer SHALL render that state rather than the older result as current.

ID: REQ-butler-chronicler-009
Source: bu-h40h2b.6 acceptance 5 and 6; bu-vpn5ij; decision A1 (bu-2o1dn8)
Scope: v1-mandatory

#### Scenario: Never-evaluated day is not-found

- **WHEN** the endpoint is read for a day with no run
- **THEN** it returns an explicit not-found and not a zero-count result

#### Scenario: Current or future day is rejected

- **WHEN** the endpoint is read for today or a later date
- **THEN** it returns not-found

#### Scenario: Response carries no attendance field

- **WHEN** a run exists
- **THEN** the response has contradicted and unknown counts, freshness and coverage, and no attended count
