# Test Infrastructure and End-to-End Testing

## Purpose
Defines the test framework configuration, test categories and markers, test infrastructure (Docker PostgreSQL testcontainers, DB fixtures, resilient teardown), E2E staging harness architecture, E2E test domains (security, state, contracts, observability, approvals, resilience, flows, scheduling, performance, infrastructure), conftest fixture hierarchy, and test naming conventions.

## Quick Start

```bash
# Install dependencies
uv sync --dev

# Run unit tests only (no Docker needed)
uv run pytest tests/ --ignore=tests/e2e --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q

# Run integration tests (requires Docker)
uv run pytest roster/ -q

# Run E2E tests (requires Docker + ANTHROPIC_API_KEY + claude CLI)
uv run pytest tests/e2e/ -v -s

# Run a single E2E scenario by ID
uv run pytest tests/e2e/test_scenario_runner.py -v -k "health-weight-log" -s

# Run a single E2E flow module
uv run pytest tests/e2e/test_health_flow.py -v -s --tb=long

# Maximum verbosity (all logs to console)
uv run pytest tests/e2e/ -v -s --log-cli-level=DEBUG --tb=long
```

## Debugging E2E Tests

### Inspecting Databases During a Test Run

While tests are running (or paused in a debugger), connect to any butler's database via the testcontainer's exposed port:

```bash
# The ecosystem fixture logs the actual port at session start.
psql -h localhost -p $EXPOSED_PORT -U test -d butlers   # then: SET search_path TO health;
```

The `butler_ecosystem` fixture is session-scoped. If you set a breakpoint, all butlers remain running on their ports and all databases remain accessible.

### Interactive Debugging

From a debugger breakpoint inside a test, you can manually call MCP tools:

```python
from fastmcp import Client as MCPClient

async with MCPClient("http://localhost:41103/sse") as client:
    result = await client.call_tool("status", {})
    print(result)
```

### Log Triage Commands

All butler logs are captured to `.tmp/e2e-logs/e2e-latest.log`:

```bash
# All errors and exceptions
grep -i 'error\|exception\|traceback' .tmp/e2e-logs/e2e-latest.log

# Errors for a specific butler
grep -i 'error.*health\|health.*error' .tmp/e2e-logs/e2e-latest.log

# LLM invocations
grep 'spawner.*trigger\|ClaudeCodeAdapter' .tmp/e2e-logs/e2e-latest.log

# MCP tool calls
grep 'tool_span\|call_tool' .tmp/e2e-logs/e2e-latest.log

# Module failures (expected for telegram, email, calendar)
grep 'Module.*disabled\|Module.*failed' .tmp/e2e-logs/e2e-latest.log

# Routing decisions
grep 'classify_message\|route.*target' .tmp/e2e-logs/e2e-latest.log
```

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    TEST HARNESS (pytest)                       │
│                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  conftest.py  │  │ scenarios.py │  │ test_*_flow.py   │    │
│  │              │  │              │  │                  │    │
│  │ Ecosystem    │  │ E2EScenario  │  │ Per-butler       │    │
│  │ bootstrap    │  │ dataclass    │  │ complex flows    │    │
│  │ + fixtures   │  │ registry     │  │                  │    │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘    │
│         │                 │                    │               │
│         ▼                 ▼                    ▼               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              BUTLER ECOSYSTEM (session-scoped)           │  │
│  │                                                          │  │
│  │  Switchboard ──► General ──► Relationship ──► Health     │  │
│  │   :41100          :41101       :41102           :41103   │  │
│  │                                                          │  │
│  │  Messenger                                               │  │
│  │   :41104                                                  │  │
│  │                                                          │  │
│  │  Each: ButlerDaemon + FastMCP SSE + Spawner + DB pool   │  │
│  └──────────────────────┬──────────────────────────────────┘  │
│                         │                                      │
│                         ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │          TESTCONTAINER PostgreSQL (session-scoped)       │  │
│  │                                                          │  │
│  │  One `butlers` database; per-butler SCHEMAS:             │  │
│  │  switchboard  general  relationship  health  messenger   │  │
│  │  plus shared `public`                                    │  │
│  │  Core tables: state, scheduled_tasks, sessions           │  │
│  │  Butler tables: measurements, contacts, butler_registry  │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## Requirements

### Requirement: Test Framework Configuration
The project SHALL use pytest with pytest-asyncio as the test runner. Configuration SHALL live in `pyproject.toml` under `[tool.pytest.ini_options]`.

#### Scenario: Async test mode
- **WHEN** pytest runs async test functions
- **THEN** `asyncio_mode = "auto"` is enabled so async tests do not require explicit `@pytest.mark.asyncio` decorators
- **AND** `asyncio_default_fixture_loop_scope = "session"` ensures session-scoped async fixtures share a single event loop

#### Scenario: Test paths
- **WHEN** pytest discovers tests
- **THEN** it searches `testpaths = ["tests", "roster"]`
- **AND** uses `--import-mode=importlib` for module resolution

#### Scenario: Warning filters
- **WHEN** tests run
- **THEN** known deprecation warnings from `websockets`, `uvicorn`, `AsyncMock`, `EmailModule`, and `health server` are filtered to avoid noise

### Requirement: Test Markers and Categories
Tests SHALL be classified into tiers of increasing scope, cost, and infrastructure: unit, smoke, integration, nightly (adapter), and e2e, plus specialized opt-in markers (benchmark, bench, discretion_bench, switchboard_bench, routing_accuracy, tool_accuracy, db, contract, perf) registered in `pyproject.toml`. Markers SHALL control which tiers execute in which environment.

#### Scenario: Unit tests (default, unmarked)
- **WHEN** a test has no marker or is marked `@pytest.mark.unit`
- **THEN** it runs with no external dependencies (no Docker, no API keys, no network)
- **AND** it validates isolated logic: parsing, validation, data transformations, pure functions

#### Scenario: Integration tests
- **WHEN** a test is marked `@pytest.mark.integration`
- **THEN** it requires Docker for testcontainers (PostgreSQL)
- **AND** if Docker is unavailable, the test is skipped via the `docker_available` check
- **AND** all tests under `roster/` that do not already declare an explicit `@pytest.mark.unit` are auto-marked as integration tests via `roster/conftest.py`
- **AND** a test (or its class/module) that explicitly declares `@pytest.mark.unit` is left as unit and runs in the unit CI lane instead, since that declaration is the author's taxonomy call and must not be silently overridden

#### Scenario: Nightly tests (adapter integration)
- **WHEN** a test is marked `@pytest.mark.nightly`
- **THEN** it is excluded from default CI via the default `addopts` deselection `-m 'not nightly and not bench and not perf'` (alongside `--import-mode=importlib -n 3 --dist loadfile --ignore=tests/benchmarks`)
- **AND** it requires the adapter's CLI binary on PATH (skipped via `skipif` when missing)
- **AND** it requires valid LLM API credentials in the environment
- **AND** it validates parser correctness against real CLI output (structural assertions only)

#### Scenario: End-to-end tests
- **WHEN** a test is marked `@pytest.mark.e2e`
- **THEN** it requires Docker (testcontainers), `ANTHROPIC_API_KEY` (real LLM calls), and the `claude` CLI binary on PATH
- **AND** it is excluded from CI/CD via three independent mechanisms: `pytest.mark.e2e` marker, environment guard (`ANTHROPIC_API_KEY` check), and explicit `--ignore=tests/e2e` in CI configuration

### Requirement: Test Directory Structure
Tests SHALL be organized into subdirectories by concern, with standalone test files for cross-cutting validations.

#### Scenario: Subdirectory organization
- **WHEN** new tests are added
- **THEN** they are placed in the appropriate subdirectory under `tests/`: `adapters` (runtime adapter tests), `api` (dashboard API tests), `cli` (CLI command tests), `config` (configuration loading tests), `connectors` (external connector tests), `core` (core component tests), `daemon` (daemon lifecycle tests), `e2e` (end-to-end tests), `features` (feature-level tests), `integration` (integration tests), `migrations` (Alembic migration tests), `modules` (module tests), `scripts` (script tests), `telemetry` (observability tests), `tools` (MCP tool tests)

#### Scenario: Standalone cross-cutting test files
- **WHEN** a test validates a cross-cutting concern
- **THEN** it may live at the top level of `tests/` (e.g., `test_education_analytics_mcp_guardrails.py`, `test_telegram_prefix_resolution.py`) or, for architectural-invariant contract tests, under `tests/contracts/`

#### Scenario: Butler-specific integration tests
- **WHEN** a butler has roster-level integration tests
- **THEN** they live under `roster/{butler-name}/tests/` and are auto-marked with the integration marker via `roster/conftest.py`

### Requirement: Conftest Fixture Hierarchy
Shared fixture definitions SHALL have one canonical module and one project-wide pytest registration layer. Nested and test-tree conftest files SHALL NOT re-register those project-wide shared fixtures; they MAY own fixtures, hooks, and helpers scoped to their tree. Root conftest SHALL retain its in-tree package-source guard before the first Butlers import and every canonical fixture, fake-engine/cache lifetime, marker, database and finalizer contract. It SHALL NOT eagerly invoke full module discovery, preload roster job sets or discover every roster router just to enable imports. Owning package import resolution SHALL make the existing supported roster namespaces available on demand without needing conftest execution.

ID: REQ-testing-039
Source: bu-ly3lv5.4 released by bu-7lh5ew; performance-discipline; actual owning source and existing contract
Scope: v1-mandatory

#### Scenario: Root conftest (conftest.py)
- **WHEN** any test in the project runs
- **THEN** `SpawnerResult`, `MockSpawner`, and `mock_spawner` are defined canonically in `src/butlers/testing/shared_fixtures.py` and imported and exported exactly once by the root `conftest.py`
- **AND** pytest makes `mock_spawner` available to both configured test trees, `tests/` and `roster/`, through that root registration
- **AND** root `conftest.py` provides the `docker_available` flag (checks `shutil.which("docker")`), `postgres_container` session-scoped fixture (`pgvector/pgvector:pg17` testcontainer), and `provisioned_postgres_pool` fixture (creates a fresh database with unique name per test invocation)

#### Scenario: Roster conftest (roster/conftest.py)
- **WHEN** tests under `roster/` run
- **THEN** `roster/conftest.py` auto-applies the `integration` marker and Docker-skip behavior to tests in that directory tree via `pytest_collection_modifyitems`, unless the test already declares an explicit `@pytest.mark.unit` (via `item.get_closest_marker("unit")`), in which case the auto-mark is skipped and the test keeps its declared `unit` taxonomy
- **AND** a meta-test (`roster/test_conftest_marker_taxonomy.py`) pins this exception so it cannot silently regress

#### Scenario: Root setup remains lightweight and complete
- **WHEN** root conftest is imported in a fresh process without a requested roster body
- **THEN** full registry, job and router discovery has not executed
- **AND** its source guard and canonical shared-fixture registration remain active

#### Scenario: A real roster request still resolves
- **WHEN** a supported roster module, job, router or models import is requested without prior root-conftest preload
- **THEN** it resolves from the actual checkout and preserves the owning discovery and registration behavior
- **AND** an unrelated roster body is not eagerly executed

### Requirement: PostgreSQL Testcontainer Infrastructure
Integration and E2E tests SHALL use Docker testcontainers for PostgreSQL, with resilient startup and teardown to handle transient Docker API errors.

#### Scenario: Session-scoped PostgreSQL container
- **WHEN** a test session starts and any test requires a database
- **THEN** a single `PostgresContainer("pgvector/pgvector:pg17")` is started and shared across all tests in the session (matching production docker-compose for pgvector and extension parity)
- **AND** individual databases within that container provide per-test isolation via unique random names (`test_{uuid_hex[:12]}`)

#### Scenario: Provisioned pool per test
- **WHEN** a test uses the `provisioned_postgres_pool` fixture
- **THEN** it receives an async context manager that creates a fresh database with a unique name, provisions it via `Database.provision()`, opens an asyncpg connection pool (configurable `min_pool_size` and `max_pool_size`), and closes the pool on test completion

#### Scenario: Resilient testcontainer startup
- **WHEN** the Docker client initialization fails with transient errors ("error while fetching server api version", "read timed out")
- **THEN** `_install_resilient_testcontainers_startup()` retries `DockerClient.__init__` up to 3 times with 0.5s backoff per attempt

#### Scenario: Resilient testcontainer teardown
- **WHEN** container removal fails with a transient Docker API error ("did not receive an exit event", "tried to kill container", "no such container", "removal of container", "is already in progress", "is dead or marked for removal", "read timed out"), matched anywhere in the exception chain including any docker-py `explanation`, or with a `requests` read timeout
- **THEN** `_install_resilient_testcontainers_stop()` retries the removal up to 4 times with exponential backoff
- **AND** on final failure, emits a `RuntimeWarning` naming the leaked container and continues rather than failing the test session

#### Scenario: Non-transient teardown failure fails fast
- **WHEN** container removal fails with anything outside that set
- **THEN** the error is raised on the first attempt rather than retried or swallowed

#### Scenario: DockerContainer.stop is patched exactly once
- **WHEN** the root conftest installs its teardown patch
- **THEN** `DockerContainer.stop` carries exactly one Butlers-owned wrapper, sitting directly over the upstream method
- **AND** the retry is applied around the container removal rather than around the whole `stop()`, so a failure in `client.close()` never re-runs a removal that already succeeded

#### Scenario: Testcontainer patches are idempotent
- **WHEN** the patches are installed at module import time
- **THEN** they are guarded by sentinel attributes (`__butlers_resilient_startup__`, `__butlers_serialized_start__`, `__butlers_resilient__`) on the patched methods to prevent double-patching

### Requirement: Pytest Run Verdicts Require a Positive Terminator
A pytest run's outcome SHALL be established by positive evidence that the run finished — a summary line, or the process exit status — never by the absence of a failure line. A run that produced neither is UNKNOWN, and UNKNOWN SHALL NOT be treated as a pass.

Where both terminators are present and the exit status alone cannot distinguish a failed run from an unfinished one, the verdict SHALL read them together. That is exactly one status: pytest's `2`, the *interrupted* run, which `--maxfail` produces on an ordinary test failure under xdist (the default parallel mode, since `addopts` carries `-n 3`; `make test-qg-serial` explicitly overrides it with `-n 0`). Reading it as UNKNOWN would make UNKNOWN the label on the most common red run there is, and UNKNOWN only carries weight while it stays rare.

#### Scenario: Truncated log has no verdict
- **WHEN** `scripts/pytest_gate.py verdict LOG` reads a log carrying neither a gate sentinel nor a pytest summary line, including the xdist truncation whose workers report `OSError: cannot send (already closed?)` after their controller is signal-killed
- **THEN** it reports `UNKNOWN` and exits `2`, so a shell `&&` chain fails closed
- **AND** it names the killed-controller signature when that marker is present, rather than reporting an undifferentiated UNKNOWN

#### Scenario: Sentinel carries the exit status and outranks the log prose
- **WHEN** a `## pytest-gate exit=N` sentinel is present
- **THEN** the verdict comes from `N` alone for every value except `2`: `0` is PASS, `1` is FAILED, and `3`, `4`, `5`, or any `128+signal` value is UNKNOWN because the suite rendered no verdict
- **AND** a nonzero sentinel outranks an earlier green summary line in the same log
- **AND** `N` of `2` is resolved by the interrupted-run scenario below, because that status alone does not say whether the run was stopped by a failure or by something that reached no verdict

#### Scenario: An interrupted run is read against its own counts
- **WHEN** the sentinel reports exit `2`, the status pytest uses for an interrupted session and therefore the status an xdist `--maxfail` run produces on an ordinary test failure
- **THEN** the log's last pytest summary line decides it: counts including `failed` or `error` are FAILED
- **AND** exit `2` with no summary line at all is UNKNOWN, because the run stopped before establishing anything
- **AND** exit `2` whose last summary line counts no failures — a `Ctrl-C` partway through a green run — is UNKNOWN and SHALL NOT be PASS, because the run never reached the tests it was interrupted before
- **BECAUSE** the summary line is itself a positive terminator, so consulting it applies the rule twice rather than relaxing it; it may take exit `2` down to FAILED and never up to PASS

#### Scenario: No other nonzero status is softened by a summary line
- **WHEN** the sentinel reports `3`, `4`, `5`, or any `128+signal` value and the log also carries a pytest summary line
- **THEN** the verdict is UNKNOWN regardless of what those counts say: exit `5` with a green summary is not a PASS, and a signal exit with a failing summary is not a FAILED
- **BECAUSE** those statuses do not report an incomplete verdict awaiting corroboration, they report that no verdict exists — nothing was collected, the gate misfired, or the run was killed — and counts printed before that cannot contradict it

#### Scenario: Summary line classified only on positive counts
- **WHEN** no sentinel is present but a pytest summary line is
- **THEN** counts including `failed` or `error` are FAILED, `no tests ran` and any summary counting no passing tests are UNKNOWN, and only a summary reporting passes with no failures or errors is PASS
- **AND** the last summary line in the log wins, so a rerun's verdict supersedes the earlier one

#### Scenario: Run receipt survives the caller being killed
- **WHEN** `scripts/pytest_gate.py run` launches pytest and the caller's process group is signalled mid-run
- **THEN** pytest continues, because the child was started in its own session
- **AND** the sentinel is appended by that child rather than by the runner, so the receipt is written even though the runner is gone

#### Scenario: Quality-gate make targets produce a receipt and a verdict
- **WHEN** `make test-qg` or `make test-qg-serial` runs
- **THEN** pytest is launched through `scripts/pytest_gate.py run` on the project interpreter (`uv run python`, never a bare `python3`, which resolves outside the venv and turns every run into `ModuleNotFoundError` -> exit 4 -> UNKNOWN), writing its log under `.tmp/test-logs/`
- **AND** the target ends with `scripts/pytest_gate.py verdict`, whose exit status is the target's, so an UNKNOWN run fails the gate instead of passing silently
- **AND** the run is mirrored to the terminal as it goes, so routing through the gate costs no interactivity

### Requirement: E2E Staging Harness Architecture
The E2E harness SHALL boot a complete disposable butler ecosystem for every test session: real ButlerDaemon processes, real PostgreSQL databases, real Alembic migrations, and real LLM calls via Haiku.

#### Scenario: Ecosystem bootstrap
- **WHEN** the E2E test session starts
- **THEN** the `butler_ecosystem` session-scoped fixture auto-discovers butlers from `roster/`, provisions one schema per butler in the shared `butlers` database, runs core and module Alembic migrations, boots each `ButlerDaemon`, starts FastMCP SSE servers on configured ports, and registers all butlers in the switchboard's `butler_registry`

#### Scenario: Butler auto-discovery
- **WHEN** a new butler is added to `roster/` with a valid `butler.toml`
- **THEN** it is automatically included in the E2E harness with zero test code changes
- **AND** it immediately participates in smoke tests (port liveness, core tables, module status)

#### Scenario: Disposable environment
- **WHEN** a test session completes (or crashes)
- **THEN** all databases are destroyed along with the testcontainer
- **AND** no state persists between runs and no test assumes state from a previous test

#### Scenario: Cost-bounded LLM usage
- **WHEN** E2E tests make LLM calls
- **THEN** they use Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for cost efficiency
- **AND** a `cost_tracker` fixture accumulates actual token counts across all calls and prints a summary at session end
- **AND** the full suite should cost approximately $0.05 to $0.20 per run

#### Scenario: E2E infrastructure container image
- **WHEN** the E2E harness starts its PostgreSQL testcontainer
- **THEN** it uses `pgvector/pgvector:pg17` (matching production `docker-compose.yml`) for migration and extension parity

### Requirement: E2E Assertion Strategy
LLM behavior is non-deterministic. The E2E harness SHALL separate infrastructure assertions (exact) from LLM-dependent assertions (loose).

#### Scenario: Structural assertions are exact
- **WHEN** validating infrastructure behavior
- **THEN** assertions are deterministic and exact: table existence checks, port liveness, session row existence, routing log entries, module status values

#### Scenario: Content assertions are loose
- **WHEN** validating LLM-dependent outcomes
- **THEN** assertions use set membership (correct butler appears in routing result), case-insensitive containment (`ILIKE '%weight%'`), numeric range checks, and existence checks (at least one matching row)
- **AND** assertions never match on exact text output from the LLM

### Requirement: E2E Declarative Scenario Framework
Simple input-output test cases SHALL be defined as `Scenario` dataclass instances in `tests/e2e/scenarios.py`. A parametrized test runner SHALL auto-generate one pytest test case per scenario.

#### Scenario: Scenario dataclass
- **WHEN** a new scenario is defined
- **THEN** it specifies: `id` (unique identifier), `description` (human-readable), `envelope` (ingest.v1 payload dict built via the `email_envelope()` / `telegram_envelope()` factory functions), `expected_routing` (target butler name, or None for multi-target), `expected_tool_calls` (subset-matched tool names), `db_assertions` (list of DbAssertion), `tags` (list, for pytest `-k` filtering), and `timeout_seconds` (default 60)

#### Scenario: DbAssertion dataclass
- **WHEN** database side effects are specified
- **THEN** each `DbAssertion` carries four fields: `butler` (str — whose DB to query), `query` (str — raw SQL to execute), `expected` (int|dict|list[dict]|None — see below), and `description` (str — human-readable label for test output)
- **AND** the `expected` field controls validation behavior:
  - **int**: the query must return a single row with a `count` column equal to this value (COUNT queries)
  - **dict**: the query must return a single row whose columns match all key/value pairs in the dict
  - **list[dict]**: the query must return multiple rows whose full list of dicts matches exactly
  - **None**: the query must return no rows (assert absence)

#### Scenario: Automatic test generation
- **WHEN** a new `Scenario` is added to `scenarios.py`
- **THEN** the parametrized runner in `test_scenario_runner.py` automatically generates a pytest test case for it with no new test functions required

### Requirement: E2E Complex Flow Tests
Multi-step scenarios that go beyond the declarative pattern SHALL be implemented as dedicated test modules.

#### Scenario: Flow test module naming
- **WHEN** a complex flow test is needed
- **THEN** it is created as `tests/e2e/test_{butler}_flow.py` or `tests/e2e/test_{concern}_flow.py`
- **AND** it uses the `butler_ecosystem` fixture to access daemon spawners, DB pools, and MCP tools

#### Scenario: Smoke tests (test_ecosystem_health.py)
- **WHEN** the E2E session starts
- **THEN** smoke tests run first with zero LLM calls, validating: every butler's SSE endpoint responds to HTTP, core tables (`state`, `scheduled_tasks`, `sessions`) exist in every database, butler-specific domain tables exist, `butler_registry` in switchboard DB has all butlers, and expected modules report correct status

#### Scenario: Switchboard flow tests (test_switchboard_flow.py)
- **WHEN** switchboard classification and dispatch are tested end-to-end
- **THEN** the test module validates: single-domain classification routes to correct butler, multi-domain decomposition produces multiple self-contained routing entries, deduplication returns `duplicate=True` with same `request_id` on second ingest, and full dispatch produces success entries in `routing_log`

#### Scenario: Cross-butler flow tests (test_cross_butler.py)
- **WHEN** the full message pipeline is tested end-to-end
- **THEN** the test validates: mock Telegram `IngestEnvelopeV1` is ingested, classification routes to correct butler, MCP dispatch succeeds, target butler spawns runtime instance, domain tools write to database, and assertions span both switchboard DB (`routing_log`, `fanout_execution_log`) and target butler DB (domain table, `sessions`)

### Requirement: E2E Security Domain
E2E security tests SHALL validate credential isolation, MCP config lockdown, database isolation, secret detection, and inter-butler communication boundaries.

#### Scenario: Credential sandbox testing
- **WHEN** testing environment variable isolation
- **THEN** a canary env var (`TEST_SECRET_CANARY`) is set, a butler is triggered, and the test asserts the runtime instance cannot access the undeclared variable
- **AND** cross-butler credential isolation is validated (health runtime cannot access relationship credentials)
- **AND** runtime authentication uses CLI-level OAuth tokens, not API keys injected via env vars

#### Scenario: MCP config lockdown testing
- **WHEN** testing MCP tool scope
- **THEN** a butler's runtime instance can only list its own tools
- **AND** attempting to call a tool from another butler returns an error
- **AND** switchboard-only tools (`classify_message`, `route`) are not available on non-switchboard butlers

#### Scenario: Database isolation testing
- **WHEN** testing cross-database boundaries
- **THEN** health butler tools do not produce rows in the relationship database
- **AND** each butler's database has its own domain tables (health has `measurements`, relationship does not)
- **AND** connection pools are scoped per-butler database

#### Scenario: Log redaction testing
- **WHEN** testing secret leakage prevention
- **THEN** `ANTHROPIC_API_KEY` value does not appear in any captured log messages after a full pipeline run
- **AND** session `tool_calls` JSONB does not contain credential values
- **AND** tool span attributes do not contain values for arguments marked as sensitive

### Requirement: E2E State Store Domain
E2E state tests SHALL validate cross-session persistence, JSONB type fidelity, state isolation between butlers, prefix listing, and concurrent access behavior.

#### Scenario: Cross-session persistence
- **WHEN** a value is written via `state_set` in one MCP client session
- **THEN** it is readable via `state_get` from a new MCP client session
- **AND** overwriting a key replaces the old value
- **AND** deleting a key causes subsequent reads to return null

#### Scenario: JSONB type fidelity
- **WHEN** values of different JSON types are round-tripped through the state store
- **THEN** strings, integers, floats, booleans, null, empty objects, empty arrays, nested objects, and unicode strings are preserved exactly

#### Scenario: State isolation between butlers
- **WHEN** two butlers write to the same key name
- **THEN** each butler's value is independent (health's `"prefs"` key is unrelated to relationship's `"prefs"` key)
- **AND** deleting a key on one butler does not affect the same key on another

#### Scenario: Concurrent state writes
- **WHEN** multiple concurrent writes target the same key
- **THEN** the final value is one of the written values (last writer wins via PostgreSQL row-level lock)
- **AND** no data corruption occurs

### Requirement: E2E Data Contract Validation Domain
E2E contract tests SHALL validate the typed data contracts between pipeline stages: IngestEnvelopeV1, Classification Response, FanoutPlan, Route Contract Version, and SpawnerResult.

#### Scenario: IngestEnvelopeV1 contract
- **WHEN** a well-formed envelope is submitted
- **THEN** it is accepted with `status="accepted"`
- **AND** wrong schema version, invalid channel/provider pair, naive datetime, and extra fields are rejected with Pydantic validation errors
- **AND** duplicate idempotency keys return `duplicate=True` with the same `request_id`

#### Scenario: Classification response contract
- **WHEN** the LLM returns a classification
- **THEN** it is a JSON array of entries with `butler`, `prompt`, and `segment` keys
- **AND** extra keys are ignored (forward-compatible)
- **AND** parse failure or empty array falls back to routing everything to `general`
- **AND** entries referencing unknown butlers are skipped

#### Scenario: SpawnerResult contract
- **WHEN** a spawner invocation completes
- **THEN** `session_id` is always set, `duration_ms` is always non-negative, `success` is true iff output is non-empty without exception, `tool_calls` is a list of `{name, arguments, result}` dicts, and token counts are set when the adapter reports usage

#### Scenario: Session persistence contract
- **WHEN** a spawner invocation occurs
- **THEN** exactly two database writes happen: `session_create()` before invocation (status="running") and `session_complete()` after with final status, duration, tokens, tool calls, and output

### Requirement: E2E Observability Domain
E2E observability tests SHALL validate distributed tracing, tool span instrumentation, routing metrics, session log completeness, and cost tracking.

#### Scenario: Trace context propagation
- **WHEN** a message traverses the full pipeline (switchboard to target butler)
- **THEN** the same `trace_id` appears in spans from both the switchboard and the target butler
- **AND** the target butler's root span has the switchboard's route span as its parent (`parent_span_id`)

#### Scenario: In-memory trace capture
- **WHEN** E2E tests run without a Grafana/Tempo endpoint
- **THEN** traces are captured in-process using an `InMemorySpanExporter` for assertion without external infrastructure

#### Scenario: Tool span instrumentation
- **WHEN** an MCP tool is invoked
- **THEN** a span is emitted with attributes: `tool.name`, `tool.butler`, `tool.module`, `tool.args` (redacted for sensitive), `tool.result.status`, and `tool.duration_ms`

#### Scenario: Session log completeness
- **WHEN** a spawner invocation completes
- **THEN** the `sessions` table row contains all required fields: `session_id` (UUID), `butler_name`, `trigger_source`, `prompt`, `model`, `status`, `created_at`, `completed_at` (for completed/error), `duration_ms`, `tool_calls` (JSONB), `input_tokens`, `output_tokens`, `trace_id`, and `error` (for error status)

#### Scenario: Cost tracking
- **WHEN** the E2E session completes
- **THEN** the `cost_tracker` fixture reports total LLM calls, input tokens, output tokens, and estimated cost
- **AND** total session cost must be under $0.20 (conservative ceiling)
- **AND** no single scenario exceeds 10,000 input tokens

### Requirement: E2E Approval Gate Domain
E2E approval tests SHALL validate the gate lifecycle: interception, approval decision, timeout, denial, and audit trail.

#### Scenario: Gated tool interception
- **WHEN** a runtime instance calls a tool configured with `approval_mode = "always"` (e.g., `contact_delete`)
- **THEN** the call is held (not executed), an approval row is created in the `approvals` table with `status = "pending"`, and the underlying data is unmodified

#### Scenario: Approval grant execution
- **WHEN** a pending approval is set to `status = "approved"`
- **THEN** the gated tool executes and produces its side effect (e.g., contact is deleted)

#### Scenario: Approval denial
- **WHEN** a pending approval is set to `status = "denied"`
- **THEN** the tool does not execute and an error is returned to the runtime

#### Scenario: Approval timeout
- **WHEN** no approval decision arrives within the timeout period
- **THEN** the approval row is marked `expired` and an error is returned to the runtime

#### Scenario: Conditional approval mode
- **WHEN** a tool is configured with `approval_mode = "conditional"` and has sensitive arguments
- **THEN** the tool is gated only when sensitive arguments (as declared in `ToolMeta.arg_sensitivities`) have non-trivial values
- **AND** calls with empty or default sensitive arguments execute without approval

#### Scenario: Approval audit trail
- **WHEN** any gated tool call occurs
- **THEN** the `approvals` table records: `tool_name`, `tool_args` (JSONB), `session_id`, `status` (pending/approved/denied/expired), `requested_at`, `decided_at`, `decided_by`, and `reason`

### Requirement: E2E Resilience Domain
E2E resilience tests SHALL validate graceful degradation under failure at every layer: infrastructure, daemon, MCP transport, LLM/spawner, and cross-butler.

#### Scenario: Butler kill and recovery
- **WHEN** a butler daemon is killed mid-operation
- **THEN** the switchboard returns `target_unavailable` when routing to the killed butler
- **AND** after the butler is restarted, routing succeeds again
- **AND** other butlers are unaffected during the outage

#### Scenario: Serial dispatch lock contention
- **WHEN** two concurrent triggers arrive for the same butler
- **THEN** both succeed serially (second waits for first to complete)
- **AND** session timestamps show non-overlapping execution windows

#### Scenario: Classification failure fallback
- **WHEN** the classification LLM fails (timeout, parse error, empty response)
- **THEN** the switchboard falls back to routing the entire message to `general` with the original text intact

#### Scenario: Partial dispatch failure
- **WHEN** dispatching a multi-domain message and one target butler is unavailable
- **THEN** the remaining subrequests execute normally (abort policy: `continue`)
- **AND** the failed subrequest is logged in `fanout_execution_log`

#### Scenario: Module startup failure isolation
- **WHEN** a module fails during startup (e.g., invalid credentials)
- **THEN** the butler starts successfully with remaining modules
- **AND** failed module's tools are not registered
- **AND** modules that depend on the failed module are marked `cascade_failed`

#### Scenario: Timeout cascade behavior
- **WHEN** a timeout fires at one layer
- **THEN** the spawner timeout logs the session with `error="timeout"` and releases the serial dispatch lock
- **AND** the route timeout produces a `routing_log` entry with `status="timeout"` and dispatch continues
- **AND** the classification timeout falls back to `general`

#### Scenario: Connection pool exhaustion
- **WHEN** the database connection pool is exhausted
- **THEN** tool calls queue on the pool rather than crashing
- **AND** after connections are returned, subsequent calls succeed

### Requirement: E2E Message Flow Domain
E2E flow tests SHALL validate the complete message pipeline from ingestion through classification, dispatch, tool execution, and database persistence.

#### Scenario: Canonical message flow
- **WHEN** a test exercises the full pipeline
- **THEN** the flow is: (1) build `IngestEnvelopeV1`, (2) `ingest_v1()` validates and persists to `message_inbox`, (3) `classify_message()` reads `butler_registry` and LLM classifies, (4) `dispatch_decomposed()` builds `FanoutPlan` and routes via MCP, (5) target butler's `trigger()` acquires lock, generates MCP config, loads CLAUDE.md, invokes runtime, (6) runtime calls domain tools, (7) test validates DB rows across both switchboard and target butler databases

#### Scenario: Declarative scenario validation
- **WHEN** a scenario specifies `expected_butler` and `db_assertions`
- **THEN** the runner dispatches to the target butler and executes each `DbAssertion` by running its raw SQL `query` against the named butler's DB pool and comparing the result to `expected`

#### Scenario: Health butler flow
- **WHEN** "Log my weight: 80kg" is sent through the pipeline
- **THEN** the switchboard classifies it to the `health` butler
- **AND** the health butler's runtime calls `measurement_log`
- **AND** the `measurements` table contains a row with type matching "weight" (case-insensitive)

#### Scenario: Relationship butler flow
- **WHEN** "Add Sarah Johnson as a new contact" is sent through the pipeline
- **THEN** the switchboard classifies it to the `relationship` butler
- **AND** the relationship butler's runtime calls `contact_add` or `contact_create`
- **AND** the `contacts` table contains a row with name matching "Sarah" (case-insensitive)

#### Scenario: Multi-domain decomposition flow
- **WHEN** a compound message like "I saw Dr. Smith and need to send her a thank-you card" is sent
- **THEN** the switchboard decomposes it into entries for both `health` and `relationship`
- **AND** each entry has a self-contained prompt with relevant context

### Requirement: E2E Scheduling Domain
E2E scheduling tests SHALL validate the TOML schedule sync, tick dispatch, cron rearm, timer/external trigger interleaving, schedule CRUD via MCP tools, and tick idempotency.

#### Scenario: TOML schedule sync
- **WHEN** a butler daemon starts
- **THEN** the `scheduled_tasks` table contains rows matching every `[[butler.schedule]]` entry in `butler.toml`
- **AND** syncing is idempotent (restarting does not duplicate rows)

#### Scenario: Due task triggers
- **WHEN** `_tick()` finds a task with `due_at` in the past
- **THEN** the task status transitions from `pending` to `running` to `completed` (or `error`)
- **AND** `due_at` advances to the next cron cycle after completion

#### Scenario: Dual-mode dispatch
- **WHEN** a scheduled task fires
- **THEN** native-mode tasks (with `dispatch_mode = "job"` and `job_name`) execute deterministic Python job handlers directly without the scheduler automatically spawning a runtime instance
- **AND** a native handler MAY explicitly invoke the daemon Spawner when required by its capability spec, while preserving the Spawner's model-catalog and timeout contracts
- **AND** runtime-mode tasks (with `prompt`) dispatch through `spawner.trigger()` with `trigger_source="schedule:<task-name>"`

#### Scenario: Timer and external trigger interleaving
- **WHEN** an external trigger and a scheduled trigger fire concurrently on the same butler
- **THEN** both succeed serially via the spawner's serial dispatch lock (one waits for the other)
- **AND** neither source is starved under repeated alternating triggers

#### Scenario: Schedule CRUD via MCP tools
- **WHEN** `schedule_create`, `schedule_list`, `schedule_update`, and `schedule_delete` tools are called
- **THEN** schedules are created in the `scheduled_tasks` table, listed, updated (cron and enabled fields), and deleted
- **AND** creating a same-named task twice results in an error or upsert, not a duplicate

#### Scenario: Tick idempotency
- **WHEN** `_tick()` runs twice in quick succession
- **THEN** only one session is created for a given task (because `due_at` is advanced after the first tick)

#### Scenario: Cross-butler cron staggering
- **WHEN** multiple butlers have the same cron expression
- **THEN** their `next_due_at` timestamps are deterministically staggered per butler name to reduce synchronized LLM bursts
- **AND** the stagger offset is bounded to at most 15 minutes and always less than the cron interval

### Requirement: E2E Performance Domain
E2E performance tests SHALL validate serial dispatch lock behavior under load, connection pool saturation, MCP transport overhead, pipeline latency budgets, and cost scaling.

#### Scenario: Serial dispatch under load
- **WHEN** 5 concurrent triggers are fired at a single butler
- **THEN** all complete successfully with sessions executed serially (non-overlapping timestamps)
- **AND** no deadlock occurs under 10 concurrent triggers

#### Scenario: Lock released on error
- **WHEN** a runtime session errors out or times out
- **THEN** the serial dispatch lock is released and subsequent triggers can acquire it

#### Scenario: Connection pool saturation
- **WHEN** many concurrent MCP tool calls exhaust the asyncpg pool
- **THEN** calls queue on the pool gracefully without crashing
- **AND** after connections are returned, subsequent calls succeed

#### Scenario: MCP client caching
- **WHEN** the switchboard routes to the same butler twice
- **THEN** the second route reuses the cached `MCPClient` (faster than creating a new one)
- **AND** if the cached client is stale (butler restarted), a new client is created automatically

#### Scenario: Pipeline latency budget
- **WHEN** the full pipeline (ingest, classify, dispatch, trigger, tool execution) runs
- **THEN** it completes within 120 seconds
- **AND** classification completes within 10 seconds
- **AND** a direct tool call completes within 1 second

#### Scenario: Cost scales linearly
- **WHEN** N messages are processed through the full pipeline
- **THEN** cost per message remains roughly constant (no prompt bloat)
- **AND** cost per message is under $0.02

### Requirement: E2E Infrastructure Domain
E2E infrastructure tests SHALL validate the staging environment: PostgreSQL testcontainer provisioning, database isolation, port allocation, Docker requirements, module degradation, and CI/CD exclusion.

#### Scenario: Per-butler schema provisioning
- **WHEN** the E2E ecosystem bootstraps
- **THEN** all butlers share a single `butlers` database within the testcontainer, and each butler gets its own PostgreSQL schema (e.g., `switchboard`, `health`, `relationship`) plus the shared `public` schema
- **AND** each schema has core tables (`state`, `scheduled_tasks`, `sessions`) plus butler-specific domain tables

#### Scenario: Offset port allocation
- **WHEN** E2E butlers start
- **THEN** each butler's production base port is shifted by `E2E_PORT_OFFSET` (11000), so the E2E stack can run alongside a live production stack without port conflicts
- **AND** the `switchboard_url` of non-switchboard butlers is patched to the switchboard's offset port

#### Scenario: Docker requirements check
- **WHEN** the E2E session starts
- **THEN** it validates: Docker daemon is running, `ANTHROPIC_API_KEY` is set, `claude` CLI is on PATH, and Python 3.12+ is available
- **AND** missing prerequisites result in `pytest.skip()` with a clear message, not a confusing traceback

#### Scenario: Module degradation during E2E
- **WHEN** modules lack external service credentials (Telegram, Email, Calendar, Memory)
- **THEN** they fail gracefully during daemon startup
- **AND** each butler retains full functionality for core MCP tools, roster-defined domain tools, and spawner/trigger operations
- **AND** smoke tests verify that failed modules report the correct status and failure phase (e.g., `telegram.status = "failed"`, `telegram.phase = "credentials"`)

#### Scenario: E2E CI/CD exclusion
- **WHEN** CI/CD runs the test suite
- **THEN** E2E tests are excluded via three independent mechanisms: pytest marker (`@pytest.mark.e2e`), environment guard (session-scoped autouse fixture skips when `ANTHROPIC_API_KEY` is not set), and explicit `--ignore=tests/e2e` in CI workflow

### Requirement: Migration Testing Approach
Database schema migrations SHALL be validated both in isolation and as part of the E2E harness to ensure Alembic migration output and runtime tool SQL are compatible.

#### Scenario: Migration-runtime compatibility
- **WHEN** the E2E harness bootstraps a butler's database
- **THEN** it runs the full Alembic migration chain (core migrations, then module migrations per enabled module)
- **AND** runtime domain tools successfully execute SQL against the migrated schema, validating that migration DDL and tool DML are compatible

#### Scenario: Dedicated migration tests
- **WHEN** migration-specific tests run (under `tests/migrations/`)
- **THEN** they validate individual migration steps: forward migration applies cleanly, expected tables and columns exist after migration, and indexes/constraints are created

### Requirement: Test Naming Conventions
Tests SHALL follow consistent naming patterns for discoverability and filtering.

#### Scenario: Test file naming
- **WHEN** a test file is created
- **THEN** it is named `test_{component}.py` for unit/integration tests or `test_{butler}_flow.py` / `test_{concern}_flow.py` for E2E flow tests

#### Scenario: Test function naming
- **WHEN** a test function is defined
- **THEN** it follows the pattern `test_{behavior_under_test}` with descriptive names that indicate the expected behavior (e.g., `test_state_persists_across_sessions`, `test_cross_db_isolation`, `test_serial_dispatch_contention`)

#### Scenario: Declarative scenario naming
- **WHEN** a `Scenario` is defined
- **THEN** its `id` follows the pattern `{channel-or-butler}-{action}` (e.g., `email-meeting-invite`, `switchboard-classify-health`, `relationship-add-contact`) and its `tags` list enables tag-based filtering via `pytest -k`

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
  `nightly`, `e2e`, and the specialized benchmark/contract/db/perf markers

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

#### Scenario: Post-merge migration-chain integrity gate
- **WHEN** a push to `main` changes a migration under any root family discovered by
  `get_all_chains()`:
  - `alembic/versions/**` for shared/core chains
  - `src/butlers/modules/*/migrations/**` for module chains
  - `roster/*/migrations/**` for butler-specific chains
- **THEN** the `Migration Chain Integrity (main)` workflow checks out the pushed
  merged SHA and runs `tests/config/test_migration_chain_head.py`
- **AND** the GitHub Actions check fails loudly if the merged tree has duplicate
  revisions or more than one Alembic head
- **AND** a focused workflow-path regression evaluates a representative migration
  change from each root family, so future chains within those families remain
  covered without a static chain count

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
The smoke tier SHALL execute in CI (`.github/workflows/ci.yml`) on every merge-group tree and every backend-applicable
pull request as a fast gate, distinct from and faster than the integration tier,
and MUST NOT pull in the E2E suite or any real LLM dependency. The dedicated-selection scenario below and the distinct/faster fast-gate description apply to scoped execution and uncovered full-mode smoke items; full derived mode does not claim a separate smoke job completed faster than the full integration run. Complete full execution MAY use exact-identity derived smoke evidence from the same successful lane population under P8; this explicitly qualifies the previous unconditional dedicated invocation while preserving its command, no-LLM/no-E2E and failure contracts.

ID: REQ-testing-034
Source: bu-ly3lv5.1 preserved smoke outcome; existing bu-r5mnn event policy and testing-and-verification; bu-ly3lv5.6 P8 full-mode proof qualification
Scope: v1-mandatory

#### Scenario: Dedicated smoke selection in CI
- **WHEN** the CI `check-preflight` job runs
- **THEN** smoke tests are selected via `-m smoke` (excluding `e2e` and any real-LLM
  paths) and run alongside the independent unit and integration shards
- **AND** a smoke failure fails the CI run

#### Scenario: No E2E or real-LLM dependency in the smoke gate
- **WHEN** the smoke step runs in CI
- **THEN** it does not require `ANTHROPIC_API_KEY` or the `claude` CLI
- **AND** `tests/e2e` is excluded from the smoke selection, consistent with the
  existing E2E CI-exclusion mechanisms

#### Scenario: Existing post-queue and docs-only smoke skips are explicit
- **WHEN** a successful classifier identifies a docs/spec-only pull request, or the workflow is a push to main after protected merge-group validation
- **THEN** check-preflight may skip under the existing event policy
- **AND** this skip cannot authorize a missing classifier verdict, a failed/cancelled preflight, a backend-applicable pull request, or a merge-group omission

#### Scenario: Exact full-lane smoke reuse preserves the same guarantee
- **WHEN** full-lane receipts prove every item selected by the dedicated smoke command actually completed successfully
- **THEN** preflight emits explicitly derived release evidence with actual commands and exact source/run/attempt identity
- **AND** any uncovered or unavailable selected item requires the original dedicated command or a non-successful preflight

### Requirement: Smoke Run Release Evidence
A smoke run SHALL emit a machine-readable release-evidence record so that a release
can be tied to concrete operational proof. A derived full-lane record SHALL retain every existing field, record actual invoked shard commands and separate smoke selector provenance, and MUST NOT claim an unexecuted dedicated command ran.

ID: REQ-testing-051
Source: bu-ly3lv5.6 P8; complete existing smoke release evidence
Scope: v1-mandatory

#### Scenario: Evidence record fields
- **WHEN** the smoke tier completes (in CI or locally with evidence enabled)
- **THEN** it records, for the run: the exact command invoked, the git commit SHA,
  the wall-clock duration, the pass/fail outcome, and the set of skipped test
  classes (e.g. tests skipped because Docker was unavailable)
- **AND** the record is captured as a CI artifact or log line that can be
  referenced from release notes

#### Scenario: Derived commands are actual execution evidence
- **WHEN** a full-lane smoke record is derived
- **THEN** its exact-command field records the actual executed shard commands and evidence_mode distinguishes derivation from the dedicated smoke command
- **AND** source SHA, actual duration/outcome/skipped classes and artifact references remain available

#### Scenario: Missing smoke provenance cannot be a release proof
- **WHEN** identity, outcomes, markers or actual execution commands are incomplete
- **THEN** the release evidence is unavailable/non-passing and the dedicated command is retained as the safe execution path

### Requirement: Schema Stand-In Parity Is Complete
A hand-provisioned table stand-in SHALL mirror the indexes its migration chain
creates, and the parity guard SHALL diff them against the real table in both
directions. An index is not decoration: a unique or partial index decides which
rows the real table accepts, so a stand-in missing one produces a table that
accepts writes production rejects while every assertion about it passes.

Foreign keys and triggers SHALL remain excluded, and the reason SHALL remain
documented where an engineer reconciling a stand-in will read it.

A stand-in whose migration chain owns a schema SHALL declare the chain/schema
pair used to materialise the real table, and the parity guard SHALL fail loudly
when that metadata does not place the real table in the stand-in's
`real_schema`.

#### Scenario: An index the chain has and the stand-in lacks fails the guard
- **WHEN** the migration chain creates an index on a stand-in's table that the
  stand-in does not declare
- **THEN** the parity guard fails and names that index, the chain that creates
  it, and the declaration to reconcile
- **AND** a unique partial index is reported the same way as any other, because
  it is the case where a stale stand-in changes which writes succeed

#### Scenario: An index the stand-in has and the chain lacks fails the guard
- **WHEN** a stand-in declares an index the migration chain does not create
- **THEN** the parity guard fails and names it as extra
- **AND** a declared index whose materialised definition differs from the
  chain's — different columns, order, uniqueness or predicate — is reported as
  mismatched rather than accepted as present

#### Scenario: The index diff is proven able to fail
- **WHEN** a copy of a stand-in is deliberately blinded by removing its declared
  indexes and diffed against the real chain
- **THEN** a test asserts the guard reports the missing index
- **AND** that test fails if the index comparison is removed from the guard, so
  the arm cannot decay into a no-op that reports green

#### Scenario: Foreign keys stay excluded so each table stays creatable alone
- **WHEN** the real chain relates two stand-in tables through a foreign key
- **THEN** the stand-in does not mirror it and the parity guard does not diff it
- **AND** the exclusion's reason is documented: `pending_actions` and
  `approval_rules` reference each other through a DEFERRABLE constraint, so
  mirroring foreign keys would leave neither table independently creatable

#### Scenario: Triggers stay excluded and say why
- **WHEN** the real chain attaches a trigger to a stand-in's table
- **THEN** the stand-in does not mirror it, and a fixture needing that behaviour
  adds it beside the `ddl()` call or takes the real chain
- **AND** the documented reason distinguishes triggers that are foreign keys
  reimplemented in plpgsql — which read a sibling table unqualified and would
  resolve against whatever `search_path` reached first — from self-contained
  ones

#### Scenario: A schema-owning chain is checked in its declared schema
- **WHEN** a stand-in's migration chain must run under a non-default schema
- **THEN** the parity fixture migrates that chain under the declared schema and
  compares the real table there with the stand-in
- **AND** a wrong chain/schema declaration fails loudly instead of passing with
  an empty real-table comparison

### Requirement: Quality-Gate Targets State Their Own Execution Mode
Each quality-gate make target SHALL state its xdist worker count on its own command line rather than inheriting one. `pyproject.toml`'s `addopts` carries `-n 3 --dist loadfile` and is prepended to every pytest invocation in this repository, so an omitted `-n` is not "the default" — it is silently three workers, and a target whose meaning depends on the worker count cannot be read from its recipe.

#### Scenario: The serial gate target really is serial
- **WHEN** `make test-qg-serial` runs
- **THEN** the pytest process it launches resolves `-n` to `0`, with `--dist` `no`, an empty `tx` list, and no xdist distributed session registered
- **AND** the target passes `-n 0` explicitly to reach that state, because `-p no:xdist` would turn the `-n 3` inherited from `addopts` into an unrecognized-argument error instead of disabling it
- **BECAUSE** the target exists for order-dependent debugging, and parallel workers reshuffle exactly the execution order it is reached for

#### Scenario: The default gate target really is parallel
- **WHEN** `make test-qg` runs
- **THEN** the pytest process it launches registers an xdist distributed session with a nonzero worker count
- **AND** the serial target's explicit `-n 0` does not reach it

#### Scenario: The guard reads the merged value, never either half alone
- **WHEN** a test pins a gate target's execution mode
- **THEN** it obtains the target's arguments by expanding the recipe (`make -n`) rather than parsing Makefile variables, and obtains the effective `-n` from a real pytest process rather than from those arguments
- **BECAUSE** the effective value does not exist until pytest merges `addopts` with argv: a Makefile grep for `-n 0` would pass while `addopts` changed the answer, which is how the serial target's documented meaning was inverted without any diff appearing to touch it

### Requirement: Fresh-Process Import Boundary and Calibrated Budget
Import-boundary contracts SHALL run in a fresh own-source interpreter and assert the actual forbidden module roots absent before demand. The root-conftest import budget SHALL use actual elapsed-time samples and a recorded numeric cap, with an independent child timeout. Original eager-source restoration and a positioned slow-import control SHALL prove the semantic and timed assertions can fail. Measurements SHALL identify exact source, interpreter, lock, command, corpus and cache context; historical/estimated gains SHALL NOT be presented as current measured improvements.

ID: REQ-testing-040
Source: bu-ly3lv5.4 released by bu-7lh5ew; performance-discipline; testing-and-verification; original no-wall-clock-gain acceptance route
Scope: v1-mandatory

#### Scenario: Parent fake cannot conceal an eager child import
- **WHEN** a fresh subprocess imports the named unused memory, approvals, daemon/API or conftest boundary
- **THEN** absence is measured from that child's sys.modules and own-source identity
- **AND** restoring the actual eager embedding import makes the semantic contract fail

#### Scenario: Numeric budget is causally enforced
- **WHEN** a measured root-conftest import exceeds its recorded calibrated cap
- **THEN** the timed contract fails independently of the absent-stack assertion
- **AND** a positioned slow-import control and restored positive establish reachability

#### Scenario: Gain claim follows its selected evidence route
- **WHEN** completion reports a quantified affected-job wall-clock gain
- **THEN** at least five named comparable before and five named after CI runs supply the p50 comparison
- **AND** otherwise the report explicitly states no wall-clock gain is claimed, preserving the original allowed route

### Requirement: Safe branch cleanup requires bound recovery and approval
Branch cleanup SHALL default to dry-run and SHALL refuse live deletion without an author-approved exact manifest, current head/PR/protection/custody checks, and independently verified durable recovery. It SHALL preserve open/live/foreign/uncertain refs and use expected-head deletion and non-overwriting recovery. The under-100 outcome SHALL remain mandatory without granting permission to delete excluded refs.

ID: REQ-testing-041
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Unreviewed dry-run cannot delete
- **WHEN** inventory includes a merged candidate and open, live, protected and uncertain companions
- **THEN** only recoverable reviewed candidates are proposed and every remote ref remains intact
- **AND** missing approval or incomplete pagination refuses apply

#### Scenario: Closed PR and changed head do not prove safety
- **WHEN** a closed PR name matches a different current head or an unreconciled unmerged ref
- **THEN** the ref remains excluded until exact history, fourteen-day stale evidence where applicable and recovery are verified
- **AND** a safely bound merged companion remains a candidate

#### Scenario: Apply drift cannot erase new work
- **WHEN** an approved ref changes or gains a PR/lease before deletion
- **THEN** apply refuses that row and preserves the new ref
- **AND** unchanged exact approved rows can be deleted with durable stage receipts

#### Scenario: Recovery and unknown acknowledgment are truthful
- **WHEN** delete acknowledgment is lost or the operator restores a deleted exact head
- **THEN** read-only reconciliation reports actual absent/same/changed/UNKNOWN and restore refuses any replacement ref
- **AND** isolated recovery reproduces the original object bytes without relying on deleted checkouts

#### Scenario: Cleanup order preserves workers
- **WHEN** an attached or dirty active worktree exists or owned worktree removal fails
- **THEN** branch cleanup remains unavailable
- **AND** ordinary owned clean terminal cleanup retains evidence outside the checkout and removes the worktree before branch references

### Requirement: Repository setting and cache pressure have independent evidence
The authorized operator SHALL enable delete_branch_on_merge, independently verify the setting, inventory/evict only approved non-main node caches, preserve genuine main-scoped node/uv cache semantics and report actual usage below ten decimal gigabytes. Cache eviction SHALL be reported as irreversible rebuildable loss, never as branch restoration or proof of a cache hit.

ID: REQ-testing-042
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Main survivor is positively witnessed
- **WHEN** approved non-main node caches are evicted with main and Playwright companions present
- **THEN** the exact main node-cache key/version remains readable and measured usage is below 10,000,000,000 bytes
- **AND** main, active/uncertain refs and non-node caches survive

#### Scenario: Setting alone is insufficient
- **WHEN** delete_branch_on_merge becomes true while old branches or pressure remain
- **THEN** setting success is recorded separately and prune/cache outcomes remain incomplete
- **AND** failure does not broaden an eviction list

#### Scenario: Cache race and rebuild preserve honesty
- **WHEN** a cache ID/ref/key changes or a needed main entry is unavailable
- **THEN** mutation refuses drift and main-presence remains unproven until real native rebuild/readback
- **AND** key existence alone does not claim frontend hit or wall-clock improvement

### Requirement: QA new branches use exact successfully refreshed main
Trusted QA preparation SHALL resolve successfully refreshed origin/main before new investigation work, record and use that exact commit, and refuse failed refresh before checkout/spawn. It SHALL preserve existing follow-up head bindings, active-worktree data and the governing QA sandbox/publication holds; routine main movement SHALL NOT trigger rebase of an active PR.

ID: REQ-testing-043
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Successful new preparation records the real base
- **WHEN** origin/main is successfully fetched and resolved before a new QA investigation
- **THEN** the new checkout uses the resolved immutable commit and source/test receipts retain it
- **AND** no existing worker checkout is reset

#### Scenario: Failed fetch does not start stale work
- **WHEN** fetch or exact commit verification fails while stale local main exists
- **THEN** no new worktree or runtime launches and a categorical refusal is retained
- **AND** a restored successful refresh reaches ordinary launch

#### Scenario: Follow-up and foreign publication contracts survive
- **WHEN** a bound existing PR needs follow-up or foreign QA isolation/publication remains unavailable
- **THEN** its original expected head and all retained source/evidence/credential boundaries remain in force
- **AND** this freshness repair supplies neither sandbox proof nor remote-deletion authority

### Requirement: Session-link guard uses validated live PR metadata
The mandatory PR session-link guard SHALL scan validated live title/body from the exact fixed repository/PR at execution rather than the frozen event copy. It SHALL fail closed on mandatory read/identity errors, preserve current trigger-head commit/trailer and review-source policies, avoid raw metadata diagnostics and retain required verdict routing without widened token/event authority.

ID: REQ-testing-044
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Corrected live body changes rerun verdict
- **WHEN** an affected author-owned PR removes its existing forbidden link and current guards rerun on unchanged source
- **THEN** the live body is scanned and passes while a private synthetic forbidden-body companion fails
- **AND** no empty commit or new public forbidden-link canary is required

#### Scenario: Stale clean payload cannot hide a new live link
- **WHEN** frozen event body is clean but the validated current private API fixture contains a forbidden link
- **THEN** the actual workflow scan fails
- **AND** a clean live companion succeeds

#### Scenario: Mandatory API failure cannot use stale fallback
- **WHEN** live metadata fetch fails, response is malformed or repository/PR identity differs
- **THEN** the mandatory guard and required verdict fail before scan success is credited
- **AND** no raw body, matched link or provider error is printed

#### Scenario: Existing metadata surfaces and permissions survive
- **WHEN** the current PR guard scans title/body and trigger-bound commits with best-effort review comments
- **THEN** title, non-trailer commit and comment positives remain detectable with exact terminal commit trailer exemption
- **AND** read-only permissions and ordinary pull_request/merge_group checkout policy are retained

### Requirement: Storm-free elapsed windows retain complete evidence
After authorized apply the operator SHALL retain both seven-day and fourteen-day complete UTC CI push-event windows with no non-main runs, under-100 remote-head readback and cache/main-survivor evidence. Incomplete queries or elapsed time SHALL remain UNKNOWN/incomplete. The selected timing route SHALL explicitly claim no wall-clock gain while preserving actual before/after state; merge_group SHALL remain the full terminal gate and every moved/removed check SHALL retain its named survivor.

ID: REQ-testing-045
Source: bu-ly3lv5.3 released run17 intent and complete original/folded clause map; engineering-bar Change Hygiene; development; security-and-secrets
Scope: v1-mandatory

#### Scenario: Seven days do not discharge fourteen
- **WHEN** seven complete days after declared apply T0 have no non-main push
- **THEN** the seven-day criterion passes and the fourteen-day criterion stays pending until its own end
- **AND** the original cannot close from source publication or a short sample

#### Scenario: Truncation and empty pages cannot produce green
- **WHEN** a query exceeds provider result limits or returns no rows without complete successful enumeration
- **THEN** the interval is UNKNOWN until bounded subdivision and complete pagination reconcile IDs/counts
- **AND** a genuine main-push positive proves the same reader path

#### Scenario: One non-main event fails the actual window
- **WHEN** a failed, cancelled, queued or successful CI push run has a non-main branch during either interval
- **THEN** that interval fails with its exact source/time identity retained
- **AND** no evidence deletion or silent T0 restart converts it to PASS

#### Scenario: No gain claim retains assurance
- **WHEN** source and approved cleanup reduce refs/cache pressure without five before/five after timing samples
- **THEN** the move explicitly claims no wall-clock gain and retains real state evidence
- **AND** required check/guards/frontend, full merge_group population and named enforcing survivors remain unchanged

### Requirement: CI Shard Ordering and Complete Timing Evidence
CI shard preparation SHALL preserve every manifest file, selected item identity, marker, loadfile fixture boundary, named watchdog/finalizer and healthy-workload calibration. Duration ordering SHALL use complete compatible source/run/attempt/lane/shard/file/node/interpreter/lock evidence, stable descending durations and lexical ties; invalid or unavailable evidence SHALL report UNKNOWN and retain the original lexical schedule. Timing observers SHALL measure real phases without exporting raw parameter, environment, credential or provider content. Existing 2x-budget intent SHALL use an evidenced complete job budget including setup and recovery, never an aspirational lane target.

ID: REQ-testing-046
Source: bu-ly3lv5.5 full original seven outcomes and twelve committed folded objects; released bu-7lh5ew; AGENTS.md test policy; testing-and-verification; performance-discipline
Scope: v1-mandatory

#### Scenario: Duration ordering preserves membership
- **WHEN** complete compatible timing evidence exists for the exact selected file and node population
- **THEN** the same files execute in stable duration-descending order with original loadfile grouping
- **AND** restoring the actual scheduler reordering defeats the positioned order assertion while the corrected scheduler succeeds

#### Scenario: Invalid timing evidence has a truthful fallback
- **WHEN** evidence is missing, stale, incomplete, nonfinite, negative, duplicated or incompatible with source, lane, shard, manifest, nodes, lock or interpreter
- **THEN** the original lexical schedule and full selected population remain
- **AND** the receipt reports UNKNOWN rather than a guessed weight or a performance pass

#### Scenario: Scheduler behavior is actually reached
- **WHEN** the pinned inherited loadfile scheduler is exercised with planted unequal file duration and item-count groups
- **THEN** the selected no-reorder mechanism preserves duration order without splitting file fixtures
- **AND** a default-reorder causal control reaches the wrong order and every original item still executes

#### Scenario: Result identity precedes sanitization
- **WHEN** the actual selected pytest items execute and their JUnit is later sanitized
- **THEN** stable complete-node digest multiplicities, phase outcomes and selected-population equality are recorded from the actual process
- **AND** redacted parameter names and identical aggregate counts are not treated as exact identity proof

#### Scenario: Timing records actual boundaries
- **WHEN** a complete named run supplies wrapper start and item phase/completion observations
- **THEN** job setup, first actual result, first one percent and final five percent are measured from actual timestamps
- **AND** missing progress, observer failure, cancellation or early maxfail leaves the affected metric UNKNOWN

#### Scenario: Watchdogs retain complete healthy-workload compatibility
- **WHEN** an ordering or timer change is proposed
- **THEN** existing item, job, UV and browser bounds, finalizers and known healthy-workload floors survive until genuinely calibrated replacement evidence exists
- **AND** a helper timeout/cancellation negative and successful healthy companion test actual reachability; twice a five-minute aspiration is not adopted

#### Scenario: Timing collection does not repartition the corpus
- **WHEN** the observer collects actual timing or falls back for unavailable data
- **THEN** the original ten manifests and full selected item multiplicities remain unchanged
- **AND** it does not adopt .6 replacement inventory, planner exemptions or extra shard membership

### Requirement: CI Orphan Database Service Retirement
The twelve current backend service jobs SHALL retire only their unused postgres:16 service and CI-injected DATABASE_URL, preserving production environment parsing and every real Testcontainer, migration, bootstrap, ordinary-role, API, roster, retry, cleanup and DB invariant. Any ambient-dependent fixture SHALL be repaired to use its genuinely provisioned explicit target. Full preflight plus five unit and five integration shards SHALL retain exact testcase identities, counts, outcomes and full terminal verdict; absence alone SHALL NOT establish successful database behavior.

ID: REQ-testing-047
Source: bu-ly3lv5.5 full original seven outcomes and twelve committed folded objects; released bu-7lh5ew; AGENTS.md test policy; testing-and-verification; performance-discipline
Scope: v1-mandatory

#### Scenario: All twelve job declarations are retired
- **WHEN** preflight, unit1..5, integration1..5 and affected jobs are inspected after the change
- **THEN** their declared PG16 service and injected ambient URL are absent
- **AND** the job names, selection/skip pairing, static commands, smoke tier, watchdogs, finalizers and full merge_group fan-in remain

#### Scenario: Provisioned PG17 remains the real target
- **WHEN** an owning DB fixture commits a sentinel under its ordinary migrated runtime role
- **THEN** a separate acquisition reads the sentinel from its actual pgvector PG17 Testcontainer
- **AND** a planted inaccessible ambient URL cannot redirect the explicit target and its deliberately ambient-only companion fails

#### Scenario: Production DB configuration remains valid
- **WHEN** an API, daemon, connector or operational caller supplies its governed explicit environment
- **THEN** existing DB URL, SSL, default and error behavior remains unchanged
- **AND** no blind global unset, fallback shared database, new principal or grant is introduced

#### Scenario: Moved service assertions retain an enforcing survivor
- **WHEN** the old CI service-presence and default-URL assertions are intentionally superseded
- **THEN** exact twelve-job absence and genuine endpoint/role/commit readback enforce the replacement contract
- **AND** every unrelated original assertion and both complete marker/roster/migration populations remain mandatory

#### Scenario: Terminal gate proves retirement scope
- **WHEN** the no-service branch and protected merge-group tree finish their full hosted runs
- **THEN** preflight and all ten shards are successful with exact baseline testcase population and coverage provenance
- **AND** Initialize containers is absent from all twelve jobs and the original under-three-second observation is separately recorded rather than inferred

### Requirement: CPU Dependency Lock and Own Source Environment Cache
The project SHALL explicitly select a compatible CPU-only torch lock source and frozen environment, with zero installed or locked NVIDIA dependencies and preserved embedding, tensor, vector-extension, image and entrypoint behavior. Retirement of unused Python pgvector or qrcode WhatsApp packaging SHALL preserve their actual governed runtime survivors. Venv and bytecode cache restoration SHALL be advisory and exact-compatible, never bypass frozen synchronization, editable source repair, own-checkout import validation or finite installation failure. Only the current job-owned real directory MAY be rebuilt; linked or foreign environments SHALL NOT be accepted.

ID: REQ-testing-048
Source: bu-ly3lv5.5 full original seven outcomes and twelve committed folded objects; released bu-7lh5ew; AGENTS.md test policy; testing-and-verification; performance-discipline
Scope: v1-mandatory

#### Scenario: CPU choice is a lock source decision
- **WHEN** the required supported Python/platform environment resolves and installs from the explicit CPU index
- **THEN** the frozen project and installed CPU tensor operation agree, CUDA is unavailable and NVIDIA match count is zero
- **AND** index metadata, UV_TORCH_BACKEND labels or a no-match rg exit code alone cannot establish installed behavior

#### Scenario: Embedding and image survivors remain
- **WHEN** CPU/package changes finish their owning tests and both actual image builds
- **THEN** model, dimension, normalization, first-use/cache and entrypoint behavior remain and before/after image sizes are recorded
- **AND** PostgreSQL vector extension, Pillow, Go bridge and unrelated extras are preserved

#### Scenario: Unused packaging retirement is causally checked
- **WHEN** a pgvector Python or qrcode-only extra/flag is removed after a complete consumer and packaging audit
- **THEN** the actual dynamic, entrypoint, Go and image consumers still succeed through their mandatory survivors
- **AND** AST import absence alone supplies no runtime or packaging completion credit

#### Scenario: Compatible warm cache repairs the current checkout
- **WHEN** an exact OS, architecture, Python ABI/micro-version, uv, lock, project, helper-layout and extras/dev cache is restored
- **THEN** a finite frozen sync with bytecode compilation and editable project repair precedes tests
- **AND** the actual interpreter and butlers import resolve the current checkout src without an external-package escape

#### Scenario: Corrupt or incompatible cache rebuilds safely
- **WHEN** a symlink, foreign pth, wrong lock/interpreter, corrupt manifest or unavailable cache is presented
- **THEN** the advisory restore is rejected or repaired and only the current job-owned real directory is rebuilt
- **AND** a full cold frozen sync and current-source positive succeed while neutralizing repair reaches the foreign-source negative

#### Scenario: Cache cannot silence install failure
- **WHEN** the cache hits but frozen synchronization or current-source import validation fails
- **THEN** the job fails with a bounded categorical receipt
- **AND** it neither uses cached successful-test evidence nor skips synchronization

#### Scenario: Cold and warm metrics stay distinct
- **WHEN** named source-compatible cold and warm runs complete
- **THEN** actual install/setup observations include cache state and complete compatibility provenance
- **AND** the original about-five-second install and twenty-second setup targets remain UNMET when not reached or measured

#### Scenario: Cached material remains private and bounded
- **WHEN** environment caching or recovery publishes a receipt
- **THEN** only compatibility digests and bounded hit/miss/invalid/repaired/import-bound categories are emitted
- **AND** credentials, env values, DSNs, provider content, shared worktree venvs and unrelated cache deletion remain excluded

### Requirement: Measured Worker and Coverage Core Selection
Unit worker and covered tracer selection SHALL be independent measured comparisons against current unit3 and ctrace baselines. Actual worker identities, capacity, memory and no-OOM evidence SHALL govern any fixed4 or bounded-auto choice; local3 and integration cap behavior SHALL remain. A sysmon candidate SHALL prove its actual supported installed tracer, complete source/file/line/context population and report/badge semantics; unsupported or fallback execution SHALL be rejected or truthfully retained as baseline. CI_COVERAGE 0/1, all ten protected covered shards and fail-closed publisher identity checks SHALL remain mandatory.

ID: REQ-testing-049
Source: bu-ly3lv5.5 full original seven outcomes and twelve committed folded objects; released bu-7lh5ew; AGENTS.md test policy; testing-and-verification; performance-discipline
Scope: v1-mandatory

#### Scenario: Auto records its actual count
- **WHEN** unit3, fixed4 and bounded-auto run comparable complete populations
- **THEN** actual worker identities, CPU allocation and memory/no-OOM observations are recorded
- **AND** the root auto cap of three is not mislabeled as four based on a runner name

#### Scenario: Worker selection preserves local and integration bounds
- **WHEN** a measured supported unit worker choice is installed
- **THEN** only the existing unit CI override changes
- **AND** local addopts3, integration cap/retry/serialization, ten shard names and exact testcase population remain

#### Scenario: Sysmon actual tracer is proved
- **WHEN** a supported covered run requests sysmon
- **THEN** the actual installed tracer and full normalized file, line and context records match the required baseline
- **AND** a planted covered-line companion proves collection and a forced fallback or unsupported branch/context/concurrency candidate is rejected

#### Scenario: Coverage mode stays exact
- **WHEN** ordinary PR, standalone or merge_group covered consumers execute
- **THEN** strict CI_COVERAGE 0/1 selection and governed coverage flags stay exact
- **AND** a worker/core experiment does not enable partial coverage, append across jobs or skip any protected shard

#### Scenario: Publisher rejects wrong evidence
- **WHEN** any shard source, attempt, lane, population, historical pseudo-src identity or upload is missing, duplicated, stale or wrong
- **THEN** the existing coverage combine/publication guard refuses it before badge publication
- **AND** genuine complete ten-source artifacts supply the positive companion

#### Scenario: Factors are compared independently
- **WHEN** worker and core candidates produce admissible comparable runs
- **THEN** each factor selects one actually supported configuration or retains a documented baseline result
- **AND** counterfactual gains are not summed and original targets/completeness remain pending without a lawful explicit root deferral

#### Scenario: No provider or fixture substitution proves throughput
- **WHEN** a measurement report claims worker/core or dependency gains
- **THEN** the exact hosted corpus, runner, interpreter, lock, cache, tracer and source are named
- **AND** synthetic conformance, shortened tests or preserved historical embedding proof cannot establish current full throughput

### Requirement: Whole Fixed Overhead Delivery and Measurement Preservation
Whole fixed-overhead delivery SHALL preserve all seven original acceptance literals, all twelve complete folded move objects, all five slices and every existing governing scenario and assertion. At least ten named genuine merge-group observations per original slice and the selected original before/after p50 or explicit no-gain route SHALL be retained. All hard first-result-about25, setup20, first-one-percent30, final-five-percent30, install-about5 and container-absence/under3 targets SHALL remain separate mandatory observations. Every removed or moved check SHALL name its enforcing survivor; normal own native adoption and final exact-head independent, hosted and protected gates SHALL precede whole closure.

ID: REQ-testing-050
Source: bu-ly3lv5.5 full original seven outcomes and twelve committed folded objects; released bu-7lh5ew; AGENTS.md test policy; testing-and-verification; performance-discipline
Scope: v1-mandatory

#### Scenario: Full original outcomes survive
- **WHEN** the proposal, source or completion packet changes
- **THEN** all seven exact literals, twelve full objects and S1..S5 map to surviving mandatory homes
- **AND** committed clipped suffixes stay unknown and no future partition/matrix/sixth-shard proposal is adopted

#### Scenario: Ten observations and p50 use real provenance
- **WHEN** completion supplies original slice timing evidence
- **THEN** ten named actual merge_group observations per original slice and at least five before/five after where p50 is claimed are reconciled
- **AND** the allowed explicit no-wall-clock-gain route erases none of the independent hard timing, DB, dependency, identity, assurance or completeness outcomes

#### Scenario: Every moved check has a survivor
- **WHEN** a service, packaging, scheduling, cache or coverage check is removed or moved
- **THEN** its full original invariant and executing enforcing survivor are identified
- **AND** all unrelated privacy, auth, retry, idempotency, migration, fixture, finalizer, FE and fail-closed contracts remain

#### Scenario: Native preparation does not claim future gates
- **WHEN** source preparation and normal validated own sync/archive finish
- **THEN** only completed prearchive tasks and actual administrative readback are marked done
- **AND** fresh final-head normal/nonself/protected and elapsed observations stay explicit mandatory closure records; no force, skip-validation, ratchet, foreign adoption or silent slice deferral occurs

#### Scenario: Rollback preserves the governed baseline
- **WHEN** a source factor fails its causal or hosted controls
- **THEN** its reviewed source/config/dependency unit is reverted or repaired with full original corpus and survivors intact
- **AND** no root or peer venv, live deployment, settings, refs, cache or elapsed evidence is modified by PRIMARY

### Requirement: Composed read-only local pre-push refusal
The repository SHALL provide an installed read-only pre-push path for its applicable deterministic guards that preserves existing Beads hook behavior and exact Git ref-update input. Actual collection-dependent inventory/budget predicates SHALL require freshly collected real selected items, not planner/cache/count inference. The hook SHALL execute no test bodies, DB/provider/network calls or tracked-tree rewrites. Installation SHALL preserve exact prior common configuration and managed hook bodies/modes, refuse uncomposable custom inputs, and be reversible. The required CI guards and complete terminal merge-group SHALL remain independently authoritative. The original twenty natural-push p95-under30s target SHALL remain mandatory and unearned until measured.

ID: REQ-testing-052
Source: bu-ly3lv5.10 approved A123/B168 R1; about/heart-and-soul/development.md; about/craft-and-care/testing-and-verification.md; AGENTS.md Test Scope Policy
Scope: v1-mandatory

#### Scenario: Actual violation and restored ordinary push
- **WHEN** the owning real push path encounters a planted new em-dash in the mandated doctrine surface
- **THEN** its actual scanner refuses before remote publication with fixed failed-target attribution
- **AND** restoring the owning source makes that same actual path healthy without skipped invariants

#### Scenario: Beads hook input and other hook lifecycles survive
- **WHEN** Git invokes root or worktree hooks with multiple ref updates and actual args
- **THEN** the original managed hook receives the complete stream exactly once and its status propagates
- **AND** post-checkout and post-merge retain BD_IMPORT_AUTO=false outside managed markers

#### Scenario: Unsupported configuration or source refuses before mutation
- **WHEN** a custom/global/worktree-specific hook path, missing asset, unknown ref/tree or applicable unavailable tool cannot be safely composed
- **THEN** installation/check refuses categorically and exact before configuration/assets remain
- **AND** a restored supported path proves positive behavior

#### Scenario: Fresh collection-dependent predicate preserves actual population
- **WHEN** a partition or collected-budget check requires current test membership
- **THEN** the real collect-only path and current predicates execute against the complete fresh population
- **AND** stale receipts, cached counts, AST test counts, planner omissions and side-effect execution cannot claim green

#### Scenario: Natural timing and rollback are separately admitted
- **WHEN** twenty natural installed push envelopes and complete statuses are available
- **THEN** p95 strictly below30s is assessed from their exact source/runtime/plan and full hook boundaries
- **AND** unavailable timing or a missed target remains unmet; rollback restores exact prior config and all enforcing CI survivors

### Requirement: Condensation requires current executable survivor evidence

The system MUST refuse condensation admission until exact source-bound survivor evidence preserves the removed behavior, complete case multiplicity and protected architecture, wire, privacy, authorization, retry, idempotency and migration outcomes. Neither test counts, similar text, source deletion, attestation nor coverage alone establishes that evidence. The consumer SHALL preserve the existing selection, budgets, default coverage core, required CI event predicates and full terminal merge-group gate.

ID: REQ-testing-053
Source: bu-ly3lv5.11 preserving A82/B146 contract; about/heart-and-soul/development.md; about/craft-and-care/testing-and-verification.md Five-Minute Routine-Lane Target; AGENTS.md Test Scope Policy
Scope: v1-mandatory

#### Scenario: Exact branch contexts and retained mutation kills
- **WHEN** removed cases and named survivors execute a protected proof
- **THEN** the producer uses actual ctrace branch arcs under literal exact per-case phase contexts and complete collected case identities
- **AND** every removed-case mutant kill is retained by a named survivor with zero normative residue and no lost kills before dogfood deletion
- **AND** setup, collection, timeout, incomplete phases or uncertain cleanup are UNKNOWN rather than semantic kills

#### Scenario: Assertion and parameter loss inside a retained name refuses
- **WHEN** an unchanged test qualname loses an assertion, parameter declaration, fixture/import context or collected case
- **THEN** the consumer detects that change and requires its complete source-bound survivor account
- **AND** matching qualnames or a smaller count cannot hide the lost invariant

#### Scenario: Moves and retirement have no automatic exemption
- **WHEN** a test moves or renames, or a production source path is deleted
- **THEN** admission still requires complete body/import/fixture/marker/case continuity or genuine governing retirement authority with a protected survivor account
- **AND** filename similarity and deleted source paths grant no permission to remove protected coverage

#### Scenario: Malformed stale or partial evidence refuses
- **WHEN** evidence has duplicate keys or aliases, wrong JSON types, missing survivors, changed relevant bodies/modes/tools/selection, or incomplete phase/outcome/restore population
- **THEN** admission refuses without automatic baseline repair or a count-only fallback
- **AND** merge-group does not permit stale proof merely because its event differs from a pull request

#### Scenario: Compatible protected union preserves dated proof identity
- **WHEN** a protected union has freshly validated event/head lineage and exactly unchanged relevant source/mode/config/tool/selection inputs
- **THEN** verification may reuse that unchanged proof within its named scope while retaining its dated original run identity
- **AND** changed relevant inputs require fresh proof rather than a historical receipt restamp

#### Scenario: Crash cancellation and timeout preserve recovery
- **WHEN** an owned proof process fails, times out, is cancelled or crashes during a mutation
- **THEN** a durable journal and scoped UNKNOWN receipt survive until exact file/mode restoration and owned process-group completion are attested
- **AND** the live source, unrelated processes, environment and other proof runs remain untouched

#### Scenario: Catalog and attestation cannot substitute for protected runtime proof
- **WHEN** a proposed deletion relies on migration/catalog behavior or protected authority
- **THEN** actual migrated PostgreSQL roles, bootstrap, constraints/defaults/indexes/functions/RLS/grants/rollback and relevant negative companions remain required
- **AND** SQL-text coverage, synthetic catalogs and attested mode cannot discharge those outcomes

#### Scenario: One guard species and complete survivor accounting
- **WHEN** the required condensation guard is activated after genuine preparation qualification
- **THEN** one stdlib source-scan consumer owns deletion admission across the complete declared tests and roster roots, including in-place losses
- **AND** every moved or removed check names its executing survivor, with no empty-directory fail-open, new selector exclusion or duplicated normative scanner

#### Scenario: Source evidence does not complete timing or delivery
- **WHEN** the additive proof machinery and its private controls pass
- **THEN** the first three real clusters, zero-residue/no-lost dogfood, shared-app isolation, original per-file/cluster and named before/after timing, supported native administration and current genuine/normal/protected/squash remain mandatory
- **AND** the generic no-wall-clock-gain option applies only to its original timing paragraph

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
