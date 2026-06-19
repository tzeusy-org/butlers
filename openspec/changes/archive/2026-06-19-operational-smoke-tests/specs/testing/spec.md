## ADDED Requirements

### Requirement: Smoke Test Tier
The project SHALL define a `smoke` test tier: fast, deterministic operational-proof
tests that prove the deterministic daemon infrastructure can start, migrate, run,
recover, and expose health. Smoke tests MUST make no real LLM calls and MUST be
suitable to run on every push as a fast CI gate. Smoke tests sit between unit and
integration tiers in cost: they MAY require Docker (PostgreSQL testcontainer) where
proving a real operational surface requires a real database, but MUST NOT require
`ANTHROPIC_API_KEY`, the `claude` CLI binary, or any runtime adapter LLM invocation.

#### Scenario: Smoke marker
- **WHEN** a test proves an operational surface (clean start, migration, daemon
  lifecycle, recovery, or health)
- **THEN** it is marked `@pytest.mark.smoke`
- **AND** the `smoke` marker is registered in `pyproject.toml` under
  `[tool.pytest.ini_options]` markers alongside the existing `unit`, `integration`,
  `nightly`, and `e2e` markers

#### Scenario: No real LLM calls in smoke tier
- **WHEN** any smoke test runs
- **THEN** it completes without `ANTHROPIC_API_KEY` set and without the `claude` CLI
  on PATH
- **AND** it uses a mock spawner (`MockSpawner` from the root conftest) or avoids
  spawning a runtime instance entirely, never invoking a real LLM

#### Scenario: Smoke tier selectability
- **WHEN** a developer runs `uv run pytest -m smoke`
- **THEN** only smoke-marked tests are collected
- **AND** the full smoke tier completes quickly (target: under 2 minutes on CI
  hardware) so it is viable as a per-push gate

### Requirement: Clean-Start Smoke Test
The project SHALL prove that, from a clean checkout, the package installs, imports,
and the `butlers` entrypoint resolves — matching the deployment path used by the
`Dockerfile` ENTRYPOINT (`uv run --frozen --no-dev butlers`).

#### Scenario: Package imports cleanly
- **WHEN** a smoke test runs after `uv sync`
- **THEN** importing the top-level `butlers` package and `butlers.cli` succeeds with
  no import-time errors or side effects requiring external services

#### Scenario: Entrypoint resolves
- **WHEN** the `butlers` console script declared in `pyproject.toml`
  (`[project.scripts]` `butlers = "butlers.cli:cli"`) is invoked with `--help`
- **THEN** it exits successfully (exit code 0) and prints usage
- **AND** the `run` subcommand referenced by the Docker CMD (`["run", "--config",
  "/etc/butler"]`) is present in the CLI surface

#### Scenario: Frozen dependency resolution parity
- **WHEN** the deployment command form `uv run --frozen --no-dev butlers --help`
  is exercisable in the smoke environment
- **THEN** it resolves the same entrypoint as the dev invocation, proving the
  frozen/no-dev install path used in the container is not broken

### Requirement: Migration Smoke Test
The project SHALL prove that the Alembic core migration chain applies cleanly from
an empty database to head, and that the latest revision survives a downgrade/upgrade
round-trip. This requirement EXTENDS the existing migration coverage in
`tests/config/test_migrations.py` (empty-to-head, idempotency, schema/table
presence) and MUST NOT duplicate assertions already made there; it adds the
fast smoke-tier framing and the latest-revision round-trip guard.

#### Scenario: Empty database to head
- **WHEN** `run_migrations(chain="core")` is applied against a freshly provisioned,
  empty PostgreSQL database with required extensions bootstrapped
- **THEN** it completes without error
- **AND** the `alembic_version` table records the current core head revision

#### Scenario: Latest revision round-trip
- **WHEN** the core chain is upgraded to head, downgraded one revision, then
  upgraded back to head
- **THEN** each step completes without error
- **AND** the schema after the round-trip is equivalent to the schema reached by a
  direct empty-to-head upgrade

#### Scenario: Reuses existing migration fixtures
- **WHEN** the migration smoke test provisions a database
- **THEN** it uses the shared migration helpers (`create_migration_db`,
  `bootstrap_extensions` from `src/butlers/testing/migration.py`) and the
  session-scoped `postgres_container` fixture rather than introducing a parallel
  provisioning path

### Requirement: Daemon Lifecycle Smoke Test
The project SHALL prove that a butler daemon completes its lifecycle initialization
to the "accepting connections" signal and then shuts down cleanly, releasing all
resources — without invoking a real LLM.

#### Scenario: Startup reaches accepting-connections
- **WHEN** a `ButlerDaemon` is started via `start()` (which delegates to
  `lifecycle.run_startup`) against a provisioned database and a mock spawner
- **THEN** startup completes and `daemon._accepting_connections` is `True`
- **AND** `daemon._started_at` is set
- **AND** the database pool is connected (`daemon.db.pool` is not `None`)

#### Scenario: Clean shutdown releases resources
- **WHEN** `shutdown()` is called on a started daemon (delegating to
  `lifecycle.run_shutdown`)
- **THEN** shutdown completes without raising
- **AND** `daemon._accepting_connections` is `False`
- **AND** background tasks (scheduler loop, liveness reporter, MCP server) are
  cancelled or awaited and database pools are closed

#### Scenario: Module startup failures do not abort the daemon
- **WHEN** a module fails during a non-fatal startup phase (e.g. missing
  credentials)
- **THEN** the daemon still reaches the accepting-connections signal
- **AND** the failed module is recorded in `daemon._module_statuses` with a
  `failed` (or `cascade_failed`) status rather than crashing startup

### Requirement: Route-Inbox Recovery Smoke Test
The project SHALL prove that durable route-inbox work survives a daemon restart:
rows left in `accepted` or `processing` state are recovered and re-dispatched on
the next startup, so no accepted work is silently lost.

#### Scenario: Unprocessed rows are scanned after restart
- **WHEN** the `route_inbox` table contains rows in `accepted` and `processing`
  state older than the recovery grace period
- **THEN** `route_inbox_scan_unprocessed` returns those rows (both states), each
  carrying `id`, `received_at`, and `route_envelope`

#### Scenario: Recovery sweep re-dispatches stuck rows
- **WHEN** `route_inbox_recovery_sweep` runs at startup with a dispatch function
- **THEN** it invokes the dispatch function once per stuck row with the row id and
  route envelope
- **AND** it returns the count of recovered rows

#### Scenario: Recovered row reaches terminal state
- **WHEN** a recovered row is dispatched and its handler completes (via the mock
  spawner)
- **THEN** the row transitions to `processed` (with `processed_at` set) via
  `route_inbox_mark_processed`, or to `errored` via `route_inbox_mark_errored` on
  failure — never remaining stuck in `processing`

### Requirement: Dashboard Health Smoke Test
The project SHALL prove that the dashboard API health surface is reachable without
authentication and reports a healthy status when the API is up.

#### Scenario: Health endpoints return healthy
- **WHEN** an HTTP GET is issued to `/api/health` and to `/health` on a running
  dashboard API
- **THEN** each returns HTTP 200 with a JSON body indicating a healthy status
  (`{"status": "ok"}`)

#### Scenario: Health is unauthenticated
- **WHEN** the health endpoints are requested without an API key
- **THEN** they succeed because both paths are in `_PUBLIC_PATHS` and bypass the
  API key and dashboard audit middleware

#### Scenario: Health reflects real liveness
- **WHEN** the dashboard API has not completed its lifespan startup
- **THEN** the health surface is not reachable / does not report healthy, so a
  green health check is evidence of a real running server rather than a static
  string returned regardless of server state

### Requirement: Smoke Tests Run In CI As A Fast Gate
The smoke tier SHALL execute in CI (`.github/workflows/ci.yml`) on every push and
pull request as a fast gate, distinct from and faster than the integration tier,
and MUST NOT pull in the E2E suite or any real LLM dependency.

#### Scenario: Dedicated smoke selection in CI
- **WHEN** the CI `check` job runs
- **THEN** smoke tests are selected via `-m smoke` (excluding `e2e` and any real-LLM
  paths) and run before or alongside the heavier integration step
- **AND** a smoke failure fails the CI run

#### Scenario: No E2E or real-LLM dependency in the smoke gate
- **WHEN** the smoke step runs in CI
- **THEN** it does not require `ANTHROPIC_API_KEY` or the `claude` CLI
- **AND** `tests/e2e` is excluded from the smoke selection, consistent with the
  existing E2E CI-exclusion mechanisms

### Requirement: Smoke Run Release Evidence
A smoke run SHALL emit a machine-readable release-evidence record so that a release
can be tied to concrete operational proof.

#### Scenario: Evidence record fields
- **WHEN** the smoke tier completes (in CI or locally with evidence enabled)
- **THEN** it records, for the run: the exact command invoked, the git commit SHA,
  the wall-clock duration, the pass/fail outcome, and the set of skipped test
  classes (e.g. tests skipped because Docker was unavailable)
- **AND** the record is captured as a CI artifact or log line that can be
  referenced from release notes

## Source References
- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure; it must be
  testable, debuggable, and predictable) — `about/heart-and-soul/vision.md`. Smoke
  tests directly prove the daemon starts, migrates, runs, recovers, and exposes
  health deterministically.
- Non-Negotiable Rule 2 (modules only add tools; they never touch core
  infrastructure) — `about/heart-and-soul/vision.md`. The daemon-lifecycle smoke
  test asserts module startup failures are isolated and never abort the
  deterministic core boot.
- `about/craft-and-care/testing-and-verification.md` — completion claims require
  evidence; verification depth scales with risk. The smoke tier is the low-cost
  operational-evidence layer, and the release-evidence requirement makes the
  "evidence before assertions" standard concrete for releases.
