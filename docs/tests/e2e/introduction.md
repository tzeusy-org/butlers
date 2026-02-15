# Introduction — Intent, Goals, and Extensibility

## What This Is

This is not a traditional test suite. It is a **modular, extensible staging
harness** that boots the full Butlers ecosystem — real `ButlerDaemon` processes,
real PostgreSQL databases, real Alembic migrations, and real LLM calls via
Haiku — in a temporary, disposable environment.

The harness validates that the complete user-facing message lifecycle works
end-to-end: external input arrives at the switchboard, gets classified by an LLM,
routes to the correct specialist butler, the butler spawns a Claude Code instance
that calls domain tools, data is persisted to the database, and a response is
generated.

It is designed to evolve beyond functional testing into a platform for load
testing, concurrency testing, chaos testing, and regression validation.

## Why End-to-End

Unit and integration tests verify components in isolation. They cannot validate
the emergent behavior of the full message lifecycle: an external message arriving
at the switchboard, being classified by an LLM into routing segments, dispatched
to the correct specialist butler via MCP, triggering a spawned Claude Code
instance that calls domain tools, and producing verifiable side effects in a
per-butler database.

This harness exists because:

1. **The routing contract is LLM-mediated.** Classification depends on the LLM
   reading the butler registry and correctly decomposing multi-domain messages.
   No amount of mocking can validate that the classification prompt, registry
   schema, and LLM behavior work in concert.

2. **The MCP transport layer is real HTTP.** Each butler runs a FastMCP SSE
   server on a real port. The switchboard connects to target butlers via
   `MCPClient` over HTTP. Transport-layer issues (SSE reconnection, client
   caching, health checks, trace context injection) only surface in a real
   network environment.

3. **The database contract spans migrations and runtime.** Alembic migrations
   create the schema. Runtime tools write to it. The harness validates that the
   migration output and the tool SQL are compatible — something no unit test
   can cover.

4. **Module degradation is a production behavior.** Modules without credentials
   (Telegram, Email, Calendar) must fail gracefully and not block the butler
   from operating with its remaining tools. This behavior must be validated in
   a real daemon lifecycle, not simulated.

## Design Goals

### 1. Full-Stack Fidelity

Every layer runs real code. No mocks except where external services require
real credentials (Telegram API, Gmail API, Google Calendar). The harness uses
the same `ButlerDaemon.start()` / `shutdown()` lifecycle as production, the same
Alembic migrations, the same FastMCP SSE transport, and the same
`ClaudeCodeAdapter` runtime (pointed at Haiku for cost efficiency).

### 2. Disposable Environments

Every test run creates a fresh environment from scratch. Testcontainers spawns
a new PostgreSQL instance with empty databases. Each butler gets its own database
(`butler_switchboard`, `butler_health`, etc.) provisioned and migrated at session
start. Nothing persists between runs. Nothing leaks. A failed test run leaves no
residual state that could affect the next run.

### 3. Cost-Bounded LLM Usage

The harness uses Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for all LLM
calls. At current pricing ($0.80/MTok input, $4.00/MTok output), a full test
suite run costs approximately $0.05–$0.20. A `cost_tracker` fixture accumulates
actual token counts across all LLM calls and prints a summary at session end.

### 4. Deterministic Infrastructure, Non-Deterministic Assertions

Infrastructure (database, ports, daemon lifecycle) is fully deterministic.
LLM behavior is not. The assertion strategy accounts for this asymmetry:
infrastructure assertions are exact (table exists, port responds, migration
applied), while LLM-dependent assertions are loose (expected butler appears
in classification result, expected table has at least one matching row).

## Modular Extensibility

The harness is designed for zero-friction extension across four independent
dimensions. Each dimension can be extended without modifying existing code.

### Dimension 1: Butlers

The `butler_ecosystem` fixture auto-discovers butlers from `roster/`. To add a
new butler to the E2E harness:

1. Create `roster/{name}/butler.toml` with a valid configuration.
2. Done.

No test code changes needed. The fixture walks `roster/`, finds every directory
containing a `butler.toml`, provisions a database, runs migrations, boots a
`ButlerDaemon`, and starts it on its configured port. The new butler becomes
immediately available to all existing test scenarios and flows.

**Why this works:** Butler discovery is filesystem-driven, not registry-driven.
The harness does not maintain a list of butlers. It discovers them the same way
the production `butlers up` command does.

### Dimension 2: Declarative Scenarios

Simple input-output test cases are defined as `E2EScenario` dataclass instances
in `tests/e2e/scenarios.py`. The parametrized test runner
(`test_scenario_runner.py`) auto-generates one pytest test case per scenario.

To add a new scenario:

```python
E2EScenario(
    id="health-symptom-log",
    description="Log a headache symptom with severity",
    input_prompt="I have a bad headache, severity 7",
    expected_butler="health",
    expected_tool_calls=["symptom_log"],
    db_assertions=[
        DbAssertion(
            butler="health",
            table="symptoms",
            where={"name": "headache"},
            column_checks={"severity": 7},
        )
    ],
    tags=("health", "symptom"),
)
```

No new test functions needed. The runner discovers it automatically.

**Why this works:** The `E2EScenario` dataclass is a complete specification of
a single-input test case: what to send, where it should route, what tools it
should call, and what database rows should exist afterward. The runner is a
generic executor that interprets these specifications.

### Dimension 3: Complex Flow Tests

For multi-step scenarios that go beyond the declarative pattern — e.g., "ingest
a message, classify it, route it, verify 3 DB tables across 2 butlers, then send
a heartbeat tick and verify the session log" — create a new test module:

```
tests/e2e/test_{butler}_flow.py
```

The module uses the `butler_ecosystem` fixture to access any daemon's spawner,
DB pool, or MCP tools. It can orchestrate arbitrarily complex multi-butler
interactions.

**Why this works:** The `butler_ecosystem` fixture exposes a handle to every
running daemon. Complex flows compose primitive operations (ingest, classify,
route, query DB) into multi-step narratives without needing any additional
infrastructure beyond what the session fixture already provides.

### Dimension 4: Test Categories Beyond Functional

The running ecosystem is a collection of real HTTP servers on real ports. This
makes it directly compatible with external testing tools and paradigms:

| Category | Approach | Entry Point |
|----------|----------|-------------|
| **Load testing** | locust / k6 / wrk against `http://localhost:8100/sse` | External tool, ecosystem fixture holds environment |
| **Concurrency testing** | Multiple concurrent `spawner.trigger()` calls | pytest tests stressing serial dispatch lock |
| **Chaos testing** | Kill butler processes mid-test | Verify switchboard returns `target_unavailable` gracefully |
| **Regression testing** | Pin scenario outputs as golden snapshots | Declarative scenarios with `expected_output` field |
| **Performance profiling** | py-spy / cProfile on daemon processes | Attach to PIDs from ecosystem fixture |

The ecosystem fixture can be extracted into a standalone `scripts/staging.py`
that holds the environment open indefinitely for interactive testing sessions.

## What It Tests (and What It Does Not)

### Tested

| Layer | What's Validated | Real or Mock? |
|-------|-----------------|---------------|
| PostgreSQL provisioning | Testcontainer DB creation + Alembic migrations | Real (testcontainers) |
| Butler daemon lifecycle | Full `ButlerDaemon.start()` / `shutdown()` | Real processes |
| FastMCP SSE servers | All butlers accept MCP tool calls on real ports | Real HTTP servers |
| Switchboard classification | LLM classifies messages to correct butler | Real Haiku LLM |
| Message routing | Switchboard dispatches to target butler via MCP | Real MCP client calls |
| Butler tool execution | Spawner invokes Claude Code, CC calls domain tools | Real Haiku LLM |
| Database persistence | Tools write to PostgreSQL (measurements, contacts, etc.) | Real DB queries |
| Session logging | Sessions table records prompt, tool_calls, duration | Real DB rows |
| Module degradation | Modules without creds (telegram, email) fail gracefully | Real daemon behavior |

### Not Tested (by design)

External service integrations (Telegram API, Gmail API, Google Calendar) require
real credentials and real external infrastructure. These are not tested in the
harness. Instead, the harness validates that modules without credentials degrade
gracefully — the same behavior seen in a production deployment where credentials
are not yet configured.

This is a deliberate design boundary, not a gap. The harness tests everything
from the internal ingestion point to the database and back. External connectors
are validated separately via integration tests with service-specific mocks.

## Guiding Principles

1. **Add butlers, not boilerplate.** Adding a butler to the roster should
   automatically bring it into the E2E harness with zero test modifications.

2. **Declare scenarios, not test functions.** Simple input-output cases belong
   in `scenarios.py` as data, not as hand-written test functions.

3. **Reserve code for complexity.** Only write a `test_{butler}_flow.py` module
   when the scenario requires multi-step orchestration, conditional logic, or
   cross-butler coordination that cannot be expressed as a declarative scenario.

4. **Fail gracefully, assert loosely.** LLM output is non-deterministic.
   Assert on structural properties (correct butler, row exists, tool was called)
   not on exact text matches.

5. **Keep it cheap.** Haiku is chosen for cost efficiency. The full suite should
   stay under $0.20 per run. The `cost_tracker` fixture makes actual cost visible
   after every run.

6. **Disposable above all.** No test should assume any state from a previous
   test. No test should leave state that affects a subsequent test. The ecosystem
   is created from nothing and destroyed completely.
