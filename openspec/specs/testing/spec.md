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
psql -h localhost -p $EXPOSED_PORT -U test -d butler_health
```

The `butler_ecosystem` fixture is session-scoped. If you set a breakpoint, all butlers remain running on their ports and all databases remain accessible.

### Interactive Debugging

From a debugger breakpoint inside a test, you can manually call MCP tools:

```python
from fastmcp import Client as MCPClient

async with MCPClient("http://localhost:40103/sse") as client:
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
│  │   :40100          :40101       :40102           :40103   │  │
│  │                                                          │  │
│  │  Messenger                                               │  │
│  │   :40104                                                  │  │
│  │                                                          │  │
│  │  Each: ButlerDaemon + FastMCP SSE + Spawner + DB pool   │  │
│  └──────────────────────┬──────────────────────────────────┘  │
│                         │                                      │
│                         ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │          TESTCONTAINER PostgreSQL (session-scoped)       │  │
│  │                                                          │  │
│  │  butler_switchboard  butler_general  butler_relationship │  │
│  │  butler_health       butler_messenger                    │  │
│  │                                                          │  │
│  │  Core tables: state, scheduled_tasks, sessions           │  │
│  │  Butler tables: measurements, contacts, butler_registry  │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## ADDED Requirements

### Requirement: Test Framework Configuration
The project uses pytest with pytest-asyncio as the test runner. Configuration lives in `pyproject.toml` under `[tool.pytest.ini_options]`.

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
Tests are classified into three tiers with increasing scope, cost, and infrastructure requirements. Markers control which tiers execute in which environment.

#### Scenario: Unit tests (default, unmarked)
- **WHEN** a test has no marker or is marked `@pytest.mark.unit`
- **THEN** it runs with no external dependencies (no Docker, no API keys, no network)
- **AND** it validates isolated logic: parsing, validation, data transformations, pure functions

#### Scenario: Integration tests
- **WHEN** a test is marked `@pytest.mark.integration`
- **THEN** it requires Docker for testcontainers (PostgreSQL)
- **AND** if Docker is unavailable, the test is skipped via the `docker_available` check
- **AND** all tests under `roster/` are auto-marked as integration tests via `roster/conftest.py`

#### Scenario: End-to-end tests
- **WHEN** a test is marked `@pytest.mark.e2e`
- **THEN** it requires Docker (testcontainers), `ANTHROPIC_API_KEY` (real LLM calls), and the `claude` CLI binary on PATH
- **AND** it is excluded from CI/CD via three independent mechanisms: `pytest.mark.e2e` marker, environment guard (`ANTHROPIC_API_KEY` check), and explicit `--ignore=tests/e2e` in CI configuration

### Requirement: Test Directory Structure
Tests are organized into subdirectories by concern, with standalone test files for cross-cutting validations.

#### Scenario: Subdirectory organization
- **WHEN** new tests are added
- **THEN** they are placed in the appropriate subdirectory under `tests/`: `adapters` (runtime adapter tests), `api` (dashboard API tests), `cli` (CLI command tests), `config` (configuration loading tests), `connectors` (external connector tests), `core` (core component tests), `daemon` (daemon lifecycle tests), `e2e` (end-to-end tests), `features` (feature-level tests), `integration` (integration tests), `migrations` (Alembic migration tests), `modules` (module tests), `scripts` (script tests), `telemetry` (observability tests), `tools` (MCP tool tests)

#### Scenario: Standalone cross-cutting test files
- **WHEN** a test validates a cross-cutting concern
- **THEN** it may live at the top level of `tests/` (e.g., `test_routing_contracts.py`, `test_tool_name_compliance.py`, `test_tool_gating.py`, `test_smoke.py`, `test_startup_guard.py`)

#### Scenario: Butler-specific integration tests
- **WHEN** a butler has roster-level integration tests
- **THEN** they live under `roster/{butler-name}/tests/` and are auto-marked with the integration marker via `roster/conftest.py`

### Requirement: Conftest Fixture Hierarchy
Fixtures are layered across three conftest files with clear scoping and re-export rules.

#### Scenario: Root conftest (conftest.py)
- **WHEN** any test in the project runs
- **THEN** it has access to fixtures from the root `conftest.py` which provides: `docker_available` flag (checks `shutil.which("docker")`), `SpawnerResult` dataclass (mock spawner output), `MockSpawner` class (configurable mock with invocation recording and result queuing), `mock_spawner` fixture (provides a MockSpawner instance), `postgres_container` session-scoped fixture (PostgreSQL 16 testcontainer), and `provisioned_postgres_pool` fixture (creates a fresh database with unique name per test invocation)

#### Scenario: Tests conftest (tests/conftest.py)
- **WHEN** tests under `tests/` run
- **THEN** `tests/conftest.py` re-exports `SpawnerResult`, `MockSpawner`, and `mock_spawner` from the root conftest to make them directly importable from the tests namespace

#### Scenario: Roster conftest (roster/conftest.py)
- **WHEN** tests under `roster/` run
- **THEN** `roster/conftest.py` auto-applies the `integration` marker and Docker-skip behavior to all tests in that directory tree via `pytest_collection_modifyitems`

### Requirement: PostgreSQL Testcontainer Infrastructure
Integration and E2E tests use Docker testcontainers for PostgreSQL, with resilient startup and teardown to handle transient Docker API errors.

#### Scenario: Session-scoped PostgreSQL container
- **WHEN** a test session starts and any test requires a database
- **THEN** a single `PostgresContainer("postgres:16")` is started and shared across all tests in the session
- **AND** individual databases within that container provide per-test isolation via unique random names (`test_{uuid_hex[:12]}`)

#### Scenario: Provisioned pool per test
- **WHEN** a test uses the `provisioned_postgres_pool` fixture
- **THEN** it receives an async context manager that creates a fresh database with a unique name, provisions it via `Database.provision()`, opens an asyncpg connection pool (configurable `min_pool_size` and `max_pool_size`), and closes the pool on test completion

#### Scenario: Resilient testcontainer startup
- **WHEN** the Docker client initialization fails with transient errors ("error while fetching server api version", "read timed out")
- **THEN** `_install_resilient_testcontainers_startup()` retries `DockerClient.__init__` up to 3 times with 0.5s backoff per attempt

#### Scenario: Resilient testcontainer teardown
- **WHEN** container stop fails with transient Docker API errors ("did not receive an exit event", "tried to kill container", "no such container", "is already in progress", "is dead or marked for removal")
- **THEN** `_install_resilient_testcontainers_stop()` retries `DockerContainer.stop()` up to 4 times with exponential backoff
- **AND** on final failure, emits a `RuntimeWarning` and continues rather than failing the test session

#### Scenario: Testcontainer patches are idempotent
- **WHEN** the patches are installed at module import time
- **THEN** they are guarded by sentinel attributes (`__butlers_resilient_startup__`, `__butlers_resilient__`, `_butlers_retry_patch`) on the patched methods to prevent double-patching

### Requirement: E2E Staging Harness Architecture
The E2E harness boots a complete disposable butler ecosystem for every test session: real ButlerDaemon processes, real PostgreSQL databases, real Alembic migrations, and real LLM calls via Haiku.

#### Scenario: Ecosystem bootstrap
- **WHEN** the E2E test session starts
- **THEN** the `butler_ecosystem` session-scoped fixture auto-discovers butlers from `roster/`, provisions a database per butler, runs core and module Alembic migrations, boots each `ButlerDaemon`, starts FastMCP SSE servers on configured ports, and registers all butlers in the switchboard's `butler_registry`

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
LLM behavior is non-deterministic. The E2E harness separates infrastructure assertions (exact) from LLM-dependent assertions (loose).

#### Scenario: Structural assertions are exact
- **WHEN** validating infrastructure behavior
- **THEN** assertions are deterministic and exact: table existence checks, port liveness, session row existence, routing log entries, module status values

#### Scenario: Content assertions are loose
- **WHEN** validating LLM-dependent outcomes
- **THEN** assertions use set membership (correct butler appears in routing result), case-insensitive containment (`ILIKE '%weight%'`), numeric range checks, and existence checks (at least one matching row)
- **AND** assertions never match on exact text output from the LLM

### Requirement: E2E Declarative Scenario Framework
Simple input-output test cases are defined as `E2EScenario` dataclass instances in `tests/e2e/scenarios.py`. A parametrized test runner auto-generates one pytest test case per scenario.

#### Scenario: E2EScenario dataclass
- **WHEN** a new scenario is defined
- **THEN** it specifies: `id` (unique identifier), `description` (human-readable), `input_prompt` (message text), `expected_butler` (target butler name), `expected_tool_calls` (list of tool names), `db_assertions` (list of DbAssertion), `tags` (for pytest `-k` filtering), `timeout_seconds` (default 30), and optional `skip_reason`

#### Scenario: DbAssertion dataclass
- **WHEN** database side effects are specified
- **THEN** each `DbAssertion` carries four fields: `butler` (str — whose DB to query), `query` (str — raw SQL to execute), `expected` (int|dict|list[dict]|None — see below), and `description` (str — human-readable label for test output)
- **AND** the `expected` field controls validation behavior:
  - **int**: the query must return a single row with a `count` column equal to this value (COUNT queries)
  - **dict**: the query must return a single row whose columns match all key/value pairs in the dict
  - **list[dict]**: the query must return multiple rows whose full list of dicts matches exactly
  - **None**: the query must return no rows (assert absence)

#### Scenario: Automatic test generation
- **WHEN** a new `E2EScenario` is added to `scenarios.py`
- **THEN** the parametrized runner in `test_scenario_runner.py` automatically generates a pytest test case for it with no new test functions required

### Requirement: E2E Complex Flow Tests
Multi-step scenarios that go beyond the declarative pattern are implemented as dedicated test modules.

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
E2E security tests validate credential isolation, MCP config lockdown, database isolation, secret detection, and inter-butler communication boundaries.

#### Scenario: Credential sandbox testing
- **WHEN** testing environment variable isolation
- **THEN** a canary env var (`TEST_SECRET_CANARY`) is set, a butler is triggered, and the test asserts the runtime instance cannot access the undeclared variable
- **AND** cross-butler credential isolation is validated (health runtime cannot access relationship credentials)
- **AND** `ANTHROPIC_API_KEY` is always present in runtime environment

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
E2E state tests validate cross-session persistence, JSONB type fidelity, state isolation between butlers, prefix listing, and concurrent access behavior.

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
E2E contract tests validate the typed data contracts between pipeline stages: IngestEnvelopeV1, Classification Response, FanoutPlan, Route Contract Version, and SpawnerResult.

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
E2E observability tests validate distributed tracing, tool span instrumentation, routing metrics, session log completeness, and cost tracking.

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
E2E approval tests validate the gate lifecycle: interception, approval decision, timeout, denial, and audit trail.

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
E2E resilience tests validate graceful degradation under failure at every layer: infrastructure, daemon, MCP transport, LLM/spawner, and cross-butler.

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
E2E flow tests validate the complete message pipeline from ingestion through classification, dispatch, tool execution, and database persistence.

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
E2E scheduling tests validate the TOML schedule sync, tick dispatch, cron rearm, timer/external trigger interleaving, schedule CRUD via MCP tools, and tick idempotency.

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
- **THEN** native-mode tasks (with `dispatch_mode = "job"` and `job_name`) execute deterministic Python jobs directly without spawning a runtime instance
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
E2E performance tests validate serial dispatch lock behavior under load, connection pool saturation, MCP transport overhead, pipeline latency budgets, and cost scaling.

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
E2E infrastructure tests validate the staging environment: PostgreSQL testcontainer provisioning, database isolation, port allocation, Docker requirements, module degradation, and CI/CD exclusion.

#### Scenario: Per-butler database provisioning
- **WHEN** the E2E ecosystem bootstraps
- **THEN** each butler gets a dedicated database (e.g., `butler_switchboard`, `butler_health`, `butler_relationship`) within the shared testcontainer
- **AND** each database has core tables (`state`, `scheduled_tasks`, `sessions`) plus butler-specific domain tables

#### Scenario: Static port allocation
- **WHEN** E2E butlers start
- **THEN** they use the same static ports as production (40100-40106)
- **AND** if the production stack is running on those ports, the E2E harness fails with `EADDRINUSE`

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
Database schema migrations are validated both in isolation and as part of the E2E harness to ensure Alembic migration output and runtime tool SQL are compatible.

#### Scenario: Migration-runtime compatibility
- **WHEN** the E2E harness bootstraps a butler's database
- **THEN** it runs the full Alembic migration chain (core migrations, then module migrations per enabled module)
- **AND** runtime domain tools successfully execute SQL against the migrated schema, validating that migration DDL and tool DML are compatible

#### Scenario: Dedicated migration tests
- **WHEN** migration-specific tests run (under `tests/migrations/`)
- **THEN** they validate individual migration steps: forward migration applies cleanly, expected tables and columns exist after migration, and indexes/constraints are created

### Requirement: Test Naming Conventions
Tests follow consistent naming patterns for discoverability and filtering.

#### Scenario: Test file naming
- **WHEN** a test file is created
- **THEN** it is named `test_{component}.py` for unit/integration tests or `test_{butler}_flow.py` / `test_{concern}_flow.py` for E2E flow tests

#### Scenario: Test function naming
- **WHEN** a test function is defined
- **THEN** it follows the pattern `test_{behavior_under_test}` with descriptive names that indicate the expected behavior (e.g., `test_state_persists_across_sessions`, `test_cross_db_isolation`, `test_serial_dispatch_contention`)

#### Scenario: Declarative scenario naming
- **WHEN** an `E2EScenario` is defined
- **THEN** its `id` follows the pattern `{butler}-{action}` (e.g., `health-weight-log`, `switchboard-classify-health`, `relationship-add-contact`) and its `tags` tuple enables tag-based filtering via `pytest -k`
