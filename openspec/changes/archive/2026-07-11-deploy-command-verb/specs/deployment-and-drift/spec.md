## ADDED Requirements

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

#### Scenario: The pipeline is idempotent across repeated or resumed runs

- **WHEN** `butlers deploy` is run again after a prior run failed at any
  phase, or after a prior run already succeeded
- **THEN** the command completes without requiring manual cleanup: image
  build reuses layer cache, the migrations container is always freshly
  created, `docker compose up -d` only recreates services whose
  configuration or image actually changed, and each invocation inserts a new
  ledger row rather than mutating a prior one
