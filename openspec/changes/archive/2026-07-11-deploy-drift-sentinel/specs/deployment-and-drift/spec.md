# Deployment and Drift — Migration-Drift Sentinel

## Purpose

Detect and surface drift between what the codebase's Alembic migrations say
should be applied and what each butler schema's database actually holds,
before it silently persists for weeks (bd bu-zhfd0: seven merged core-chain
revisions sat dark in prod with nothing able to notice). Complements the
deployment ledger (`openspec/specs/system-overview-page`, "Deployment Ledger
Facts") — the ledger records what a boot *recorded* as deployed; this
capability actively checks whether the live schema state actually matches the
codebase, on an ongoing hourly cadence, independent of when the last boot
happened.

## ADDED Requirements

### Requirement: Three-Way Migration Drift Comparison

The system SHALL compare, per butler schema, the codebase's current Alembic
head revision for every migration chain applicable to that schema against
the revision(s) actually present in that schema's `alembic_version` table.

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
comparison result plus escalation state, following the fleet-wide
degraded-envelope convention (never a fabricated all-clear, never an
unhandled 503 for a known degraded state).

#### Scenario: Endpoint returns the comparison result

- **WHEN** `GET /api/system/drift` is called
- **THEN** the response body contains:
  - `checked_at: string` -- ISO 8601 UTC timestamp of this comparison
  - `is_drifted: boolean`
  - `drifted: DriftEntry[]` -- one entry per `(schema, chain)` pair out of
    sync, each with `schema_name`, `chain`, `expected_head`, and
    `actual_revision: string | null`
  - `first_detected_at: string | null` -- when the current drift composition
    was first detected, or `null` if not drifted
  - `escalated: boolean` -- whether a QA case has already been opened for
    the current drift composition
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

The system SHALL escalate a drift composition to a QA-visible case once it
has persisted, without resolution, for more than 24 hours — and SHALL NOT
escalate the same drift composition more than once.

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

### Requirement: Red Clause on the /system Page

The `/system` dashboard page SHALL surface migration drift as a distinct,
visually red clause when drifted, and SHALL fold it into the page's overall
verdict banner.

#### Scenario: Drift renders as a red tile

- **WHEN** `is_drifted` is `true`
- **THEN** the `/system` page's drift tile renders a red badge naming the
  count of drifted chains, plus one line per drifted `(schema, chain,
  expected_head, actual_revision)` triple

#### Scenario: Verdict banner surfaces drift as a problem, not silently

- **WHEN** the page's computed verdict banner (`SystemVerdictBanner`) is
  rendered and drift is present
- **THEN** the banner's problem list includes a line naming the drifted
  chain count, annotated with "escalated to QA" once `escalated` is `true`
- **AND** the banner never renders its "all clear" state while the drift
  source is still loading or has failed to load

## Source References

- `about/heart-and-soul/vision.md` §"What Success Looks Like": "Butlers
  succeeds when it runs for weeks without intervention" -- merged-but-
  undeployed drift is exactly the kind of silent failure that doctrine line
  rules out.
- `about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md`:
  the QA case-tracking lifecycle (`public.healing_attempts`) this capability
  reuses for escalation, including the terminal-state human-action
  convention distinguishing "needs a human" cases from code-bug
  investigations.
- `openspec/specs/system-overview-page` ("Deployment Ledger Facts"): the
  sibling deployments-ledger capability this sentinel complements; that
  requirement's "migration_head is a representative snapshot" scenario
  explicitly forward-references this capability.
