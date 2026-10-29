## REMOVED Requirements

### Requirement: `butlers deploy` — Idempotent Production Deploy Verb

Reason: Helm is the sole production deploy path; release recording survives below.
Migration: use deploy/helm/butlers Make targets and `butlers deployments record`.

### Requirement: `butlers deploy` — Preflight Guard Against a Frozen or Divergent Deploy Root

Reason: the entire Compose production pipeline is removed, including its override.
Migration: use the retained Helm deployment workflow.

## ADDED Requirements

### Requirement: Legacy deployment entrypoints are retired

ID: REQ-deployment-and-drift-001
Source: bu-dss7he.14; bu-viat6h.5
Scope: v1-mandatory

The system SHALL reject the retired deploy command and production/observability
Compose flags before configuration reads or deployment effects. Helm release
recording, public.deployments and cluster monitoring assets SHALL survive.
Development Compose and its protected restore-drill isolation SHALL remain until
the separately governed complete retirement.

#### Scenario: Removed entrypoints refuse without effects

- **WHEN** an operator invokes `butlers deploy`, or the Compose launcher with
  `--prod` or `--observability`
- **THEN** argument parsing rejects it before cluster, database or lifecycle operations

#### Scenario: Helm records releases and dev isolation survives

- **WHEN** a Helm deployment completes or fails
- **THEN** the retained deployment recording command records its actual outcome
- **AND** dev Compose retains its cluster-ownership guard and explicit protected executor selection

### Requirement: Helm deployment recording

ID: REQ-deployment-and-drift-002
Source: bu-viat6h.5; bu-dss7he.14
Scope: v1-mandatory

Helm releases SHALL retain deployment ledger recording and representative core
migration-head resolution, independently of retired Compose orchestration.

#### Scenario: A Helm release is recorded as a deploy attempt, success or failure

- **WHEN** `make deploy-<env>` (or `make ship-<env>`) runs a Helm release of
  `deploy/helm/butlers`
- **THEN** it appends exactly one `public.deployments` row with `source:
  "deploy"` and `serving_mode: "image"`, written by `butlers deployments
  record` inside the `dashboard-api` pod so no database credential leaves the
  cluster
- **AND** the row has `result: "success"` only after `dashboard-api`, `butlers-up` and
  the optional enabled `butlers-control` rollouts finished, and `result: "failed"` when `helm upgrade`
  or any required rollout or its discovery failed
- **AND** the target exits with the release's own status; an unreachable pod
  prints that no row was recorded and a failed write after a successful release
  only warns, so a failed release is never reported as success

#### Scenario: The migration_head read resolves the core chain from a schema that tracks it

- **WHEN** the Helm deployment-recording command reads `migration_head` for a ledger row
  (via `butlers.core.deployments.resolve_core_migration_head`)
- **THEN** the head is resolved by discovering, through the Postgres catalog
  (`pg_catalog.pg_class`/`pg_namespace`), the schemas that physically carry an
  `alembic_version` table and reading the core-chain (`core_NNN`) head from
  them — NOT by assuming a single canonical
  schema such as `public`, which on the live deployment carries cross-butler
  tables but no `alembic_version` (the core chain is applied per butler schema)
- **AND** when no schema tracks the core chain, `migration_head` is recorded as
  `null` (an honest unknown) rather than failing the deploy
- **AND** a missing `alembic_version` table is treated as legitimately-absent —
  logged at `debug` with no traceback — while a genuine failure (dropped
  connection, permission error) still logs loudly
- **AND WHEN** the tracking schemas disagree on the core head (an anomalous
  half-applied state), the newest head is recorded and the divergence is logged
  at `warning`; authoritative per-schema drift detection remains the separate
  hourly sentinel's responsibility
