## MODIFIED Requirements

### Requirement: `butlers deploy` — Idempotent Production Deploy Verb

The system SHALL provide a single `butlers deploy` command that builds,
migrates, recreates, verifies, and records one production deploy, safe to
re-run at any point in the pipeline.

#### Scenario: A deploy builds the image stamped with the current git SHA

- **WHEN** `butlers deploy` runs
- **THEN** it resolves the deploy host's current `git rev-parse HEAD` and
  builds the `butlers-app` image with that value passed as the `GIT_SHA`
  build argument

#### Scenario: Migrations are always force-rerun, never trusted from a stale container

- **WHEN** the migrate phase runs
- **THEN** it invokes `docker compose run --rm migrations` (not `up -d`),
  which always creates a fresh container regardless of whether a prior
  `migrations` container from an older image already exited successfully
- **AND** a migration failure raises without proceeding to recreate services

#### Scenario: Service recreation never selects a compose profile

- **WHEN** the recreate phase runs
- **THEN** the `docker compose ... up -d --remove-orphans` invocation passes
  no `--profile` flag under any configuration
- **AND** the subprocess environment has `COMPOSE_PROFILES` stripped before
  the call, so an ambient `COMPOSE_PROFILES` value inherited from the calling
  shell (e.g. left over from a dev session) cannot cause the hotreload or dev
  compose profiles to be included in the recreated service set

#### Scenario: Health is polled with a bounded timeout before declaring success

- **WHEN** the recreate phase completes without error
- **THEN** the command polls the dashboard `/health` endpoint until it
  returns HTTP 200 with `status: "ok"`, or a configured timeout elapses
- **AND** a timeout is treated as a deploy failure, not a silent success

#### Scenario: Every deploy attempt is recorded to the ledger, success or failure

- **WHEN** any phase (build, migrate, recreate, health-check) fails
- **THEN** the command records a `result: "failed"` row to
  `public.deployments` (via `butlers.core.deployments.record_deployment`)
  before raising, including a best-effort `migration_head` read and the
  resolved `git_sha`
- **AND WHEN** every phase succeeds
- **THEN** the command records a `result: "success"` row with the same
  fields
- **AND** a failed deploy is therefore always visible in the ledger — never
  silently absent

#### Scenario: The migration_head read resolves the core chain from a schema that tracks it

- **WHEN** the `butlers deploy` verb reads `migration_head` for a ledger row
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

#### Scenario: The pipeline is idempotent across repeated or resumed runs

- **WHEN** `butlers deploy` is run again after a prior run failed at any
  phase, or after a prior run already succeeded
- **THEN** the command completes without requiring manual cleanup: image
  build reuses layer cache, the migrations container is always freshly
  created, `docker compose up -d` only recreates services whose
  configuration or image actually changed, and each invocation inserts a new
  ledger row rather than mutating a prior one

### Requirement: `butlers deploy` — Preflight Guard Against a Frozen or Divergent Deploy Root

The system SHALL, before any build/migrate/recreate/health-check/record step,
reject a deploy whose `--dir` root is a linked git worktree or whose `HEAD` is
not an ancestor of `origin/main`, either of which would let the live stack
serve stale code baked from a frozen worktree checkout. An operator MAY override the guard for an intentional branch deploy,
in which case both rejections are downgraded to loud warnings.

#### Scenario: A linked-worktree deploy root is rejected

- **WHEN** the deploy root's `.git` is a file (a `gitdir:` pointer, the
  filesystem tell of a `git worktree add` checkout) rather than a directory
- **THEN** the preflight refuses the deploy before any build step, raising a
  clear error that names the offending path and explains that deploys must run
  from the canonical main checkout so `docker build` bakes committed code
  rather than a frozen worktree snapshot
- **AND** no `public.deployments` ledger row is written for a refused deploy
  (a refusal to deploy is not a recorded deploy attempt)

#### Scenario: A HEAD that is not an ancestor of origin/main is rejected

- **WHEN** the deploy root's `HEAD` is not an ancestor of `origin/main` (a
  best-effort `git fetch origin main` runs first so the check reflects the true
  remote head; a fetch failure degrades to the last-known `origin/main` with a
  warning rather than failing the deploy)
- **THEN** the preflight refuses the deploy before any build step, raising a
  clear error that reports how divergent the checkout is (commits ahead of and
  behind `origin/main`)

#### Scenario: The override downgrades both rejections to warnings

- **WHEN** the deploy is invoked with the `--allow-dirty-root` override (CLI)
  / `allow_dirty_root=True` (programmatic)
- **THEN** a linked-worktree root and/or a non-ancestor `HEAD` does not raise;
  each violation is logged as a loud warning and surfaced on the command
  output, and the deploy proceeds and records its real (possibly divergent)
  `git_sha` to the ledger — that divergent SHA is itself the durable record
  that a non-main commit was deployed, which the drift sentinel compares
  against main

#### Scenario: The guard applies to both programmatic and CLI deploys

- **WHEN** a deploy is initiated either through the `butlers deploy` CLI command
  or through the programmatic `run_deploy` entry point
- **THEN** the same preflight guard runs in both paths — the CLI does not carry
  a guard the library entry point lacks
