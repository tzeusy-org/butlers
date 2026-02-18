# Infrastructure — Staging Environment Specification

## Overview

The E2E harness creates a complete, disposable staging environment for every
test session. This document specifies every infrastructure component: how it is
provisioned, what it provides, how it is torn down, and what isolation guarantees
it offers.

## PostgreSQL — Testcontainers

### Container Image

The harness uses `pgvector/pgvector:pg17` via Python testcontainers. This is the
same image used in the production `docker-compose.yml`, ensuring migration and
extension parity.

```python
# conftest.py (session-scoped)
from testcontainers.postgres import PostgresContainer

with PostgresContainer("pgvector/pgvector:pg17") as pg:
    yield pg
```

The container is **session-scoped**: one PostgreSQL instance per pytest session.
Individual databases within that instance provide per-butler isolation.

### Why Testcontainers (Not docker-compose)

The production `docker-compose.yml` maps PostgreSQL to host port `54320`. The
harness cannot use this because:

1. **Port conflicts.** The developer may have the production stack running on
   `54320` during a test run. Testcontainers automatically allocates a random
   ephemeral host port, eliminating any possibility of collision.

2. **Lifecycle ownership.** The test session must own the container lifecycle.
   Testcontainers guarantees that the container starts before any test runs and
   is destroyed after the session ends, even on crash or `KeyboardInterrupt`.

3. **Parallel safety.** Multiple test sessions (e.g., pytest-xdist workers) can
   run concurrently, each with their own testcontainer. A shared
   `docker-compose` PostgreSQL cannot support this.

### Database Isolation

Each butler gets a dedicated database within the shared testcontainer. This
mirrors the production architecture where each butler owns an isolated
PostgreSQL database.

| Butler | Database Name | Core Tables | Butler-Specific Tables |
|--------|---------------|-------------|----------------------|
| switchboard | `butler_switchboard` | state, scheduled_tasks, sessions | message_inbox, routing_log, butler_registry, fanout_execution_log |
| general | `butler_general` | state, scheduled_tasks, sessions | collections, entities |
| relationship | `butler_relationship` | state, scheduled_tasks, sessions | contacts, interactions, reminders |
| health | `butler_health` | state, scheduled_tasks, sessions | measurements, medications, symptoms |
| messenger | `butler_messenger` | state, scheduled_tasks, sessions | notification_log |
| heartbeat | `butler_heartbeat` | state, scheduled_tasks, sessions | (core only) |

**Provisioning sequence per butler:**

1. `Database.provision()` — creates the database if it does not exist
2. `Database.connect()` — opens an asyncpg connection pool (2–10 connections)
3. `run_migrations()` — runs core Alembic migrations (state, scheduled_tasks,
   sessions tables)
4. Module migrations — each enabled module runs its own Alembic migration chain

After the session, all databases are destroyed along with the testcontainer.

### Connection Parameters

Testcontainers maps PostgreSQL's internal port `5432` to a random ephemeral host
port. The harness extracts connection parameters from the container object:

```python
host = postgres_container.get_container_host_ip()   # typically "localhost"
port = postgres_container.get_exposed_port(5432)     # random ephemeral port
user = postgres_container.username                   # "test"
password = postgres_container.password               # "test"
```

These are passed to `Database(...)` for each butler. No environment variables
are needed for database connectivity during E2E tests — the testcontainer API
provides everything programmatically.

### Inspecting Databases During a Test Run

While tests are running (or paused in a debugger), you can connect to any
butler's database:

```bash
# The testcontainer maps port 5432 to a random host port.
# The ecosystem fixture logs the actual port at session start.
psql -h localhost -p $EXPOSED_PORT -U test -d butler_health
```

The `butler_ecosystem` fixture is session-scoped. If you set a breakpoint, all
6 butlers remain running on their ports and all databases remain accessible.

### Testcontainer Resilience

The harness patches testcontainers to handle transient Docker API errors that
occur during container startup and teardown. These are common in CI environments
and under pytest-xdist:

**Startup resilience** (`_install_resilient_testcontainers_startup`):
- Retries `DockerClient.__init__` up to 3 times with 0.5s backoff
- Handles transient `error while fetching server api version` and `read timed out`

**Teardown resilience** (`_install_resilient_testcontainers_stop`):
- Retries `DockerContainer.stop()` up to 4 times with exponential backoff
- Handles transient `did not receive an exit event`, `no such container`,
  `is already in progress`, `tried to kill container`, `is dead or marked for removal`
- On final failure, emits a `RuntimeWarning` and continues rather than
  failing the test session

These patches are installed at module import time in `conftest.py` and are
idempotent (guarded by sentinel attributes on the patched methods).

## Port Allocation

### Butler Port Assignments

Each butler runs a FastMCP SSE server on a deterministic port. The port is
configured in the butler's `butler.toml` and is the same in the E2E harness as
in production:

| Butler | Port | Protocol | Endpoint |
|--------|------|----------|----------|
| switchboard | 8100 | HTTP/SSE | `http://localhost:8100/sse` |
| general | 8101 | HTTP/SSE | `http://localhost:8101/sse` |
| relationship | 8102 | HTTP/SSE | `http://localhost:8102/sse` |
| health | 8103 | HTTP/SSE | `http://localhost:8103/sse` |
| messenger | 8104 | HTTP/SSE | `http://localhost:8104/sse` |
| heartbeat | 8199 | HTTP/SSE | `http://localhost:8199/sse` |

### Why These Ports

The port range `8100–8199` is chosen because:

1. **Above the privileged range.** No `sudo` or `CAP_NET_BIND_SERVICE` required.
2. **Below common development ports.** Does not conflict with typical dev server
   ports (3000, 5173, 8000, 8080, 8200, 9000).
3. **Contiguous and predictable.** Butlers are numbered sequentially from 8100.
   The heartbeat butler uses 8199 as a sentinel (last in the range).
4. **Dashboard lives at 8200.** The dashboard API runs at 8200, cleanly separated
   from the butler MCP port range.

### Port Conflict Avoidance

The E2E harness **does not** dynamically allocate ports. It uses the same static
ports as production. This means:

- If the production stack is running (`butlers up`), the E2E harness will fail
  to bind. **Stop the production stack before running E2E tests.**
- If another process occupies any port in the 8100–8199 range, the harness will
  fail with an `EADDRINUSE` error for that specific butler.

This is an intentional design choice. Dynamic port allocation would require
rewriting the switchboard's butler registry to discover ports at runtime, adding
complexity for a scenario (port conflict) that is easily avoided by stopping
other services.

### Port Verification (Smoke Tests)

The `test_ecosystem_health.py` smoke tests verify that every butler's port is
responding before any LLM-dependent tests run:

```python
# Verifies HTTP 200 on each butler's /sse endpoint
async with MCPClient(f"http://localhost:{port}/sse") as client:
    result = await client.call_tool("status", {})
    assert result is not None
```

These smoke tests run with zero LLM calls and serve as a fast gate: if any
butler failed to start, the smoke tests fail immediately and no expensive
LLM-dependent tests are attempted.

## Docker Requirements

### Host Prerequisites

| Requirement | Why | Check Command |
|-------------|-----|---------------|
| Docker daemon | Testcontainers needs a running Docker daemon | `docker info` |
| `ANTHROPIC_API_KEY` | Real LLM calls to Haiku | `echo $ANTHROPIC_API_KEY` |
| `claude` CLI on PATH | `ClaudeCodeAdapter` invokes the `claude` binary | `which claude` |
| Python 3.12+ | Project minimum | `python --version` |
| `uv sync --dev` | Install all dev dependencies | Run before first test |

All prerequisites are checked at session start. Missing prerequisites result in
a `pytest.skip()` with a clear message, not a confusing traceback.

### Docker Socket Access

Testcontainers requires access to the Docker daemon socket (`/var/run/docker.sock`).
In most development environments this is available by default. In CI or
containerized environments, the socket must be explicitly mounted.

### Image Pull

The first run will pull `pgvector/pgvector:pg17` if not already cached locally.
This is a one-time ~400MB download. Subsequent runs use the cached image.

## Module Degradation

Modules that require external service credentials will fail to initialize during
E2E tests. This is **expected and by design** — it validates the same graceful
degradation path that occurs in production when credentials are not yet
configured.

### Expected Module Failures

| Module | Required Credentials | Failure Phase | Impact |
|--------|---------------------|---------------|--------|
| telegram | `BUTLER_TELEGRAM_TOKEN` | `credentials` | No telegram tools registered |
| email | `EMAIL_ADDRESS`, `EMAIL_PASSWORD` | `credentials` | No email tools registered |
| calendar | Google OAuth credentials | `credentials` | No calendar tools registered |
| memory | pgvector extension + embeddings | `startup` (varies) | Episodic memory unavailable |

### What Still Works

Despite module failures, each butler retains full functionality for:

- **Core MCP tools:** `status`, `trigger`, `state_get`, `state_set`, `state_list`,
  `state_delete`, `schedule_create`, `schedule_list`, `schedule_update`,
  `schedule_delete`, `sessions_list`, `sessions_get`, `sessions_summary`,
  `sessions_daily`, `top_sessions`, `schedule_costs`
- **Roster-defined domain tools:** Tools defined in `roster/{butler}/tools/`
  register directly on the FastMCP server and use the butler's database. They
  are not gated by module initialization.
- **Spawner and trigger:** The LLM CLI spawner works independently of modules. It
  generates an MCP config, invokes the runtime adapter, and records the session.

### Module Status Verification

The `test_ecosystem_health.py` smoke tests verify that each butler's `status`
tool reports the expected module states:

```python
result = await client.call_tool("status", {})
# Expected: core modules healthy, external modules failed
assert result["modules"]["telegram"]["status"] == "failed"
assert result["modules"]["telegram"]["phase"] == "credentials"
```

## CI/CD Exclusion

The E2E tests are excluded from CI/CD via three independent mechanisms. Any
single mechanism is sufficient; all three are present for defense in depth.

### 1. Pytest Marker

All tests in `tests/e2e/` are marked `@pytest.mark.e2e`. The quality gate
(`make test-qg`) excludes them via `--ignore=tests/e2e`.

### 2. Environment Guard

A session-scoped autouse fixture skips all E2E tests when `ANTHROPIC_API_KEY`
is not set (which it never is in CI):

```python
@pytest.fixture(scope="session", autouse=True)
def _require_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping E2E tests")
```

### 3. CI Explicit Ignore

`.github/workflows/ci.yml` adds `--ignore=tests/e2e` to the pytest invocation.

## Cost Model

### LLM Pricing

**Model**: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`)
**Input**: $0.80 / MTok
**Output**: $4.00 / MTok

### Per-Suite Cost Breakdown

| Scenario Category | LLM Calls | Input Tokens | Output Tokens | Est. Cost |
|-------------------|-----------|-------------|---------------|-----------|
| Classification (switchboard) | 4 | ~6,000 | ~1,200 | ~$0.010 |
| Health spawner (measurement + medication) | 2 | ~6,500 | ~1,800 | ~$0.012 |
| Relationship spawner (contact) | 1 | ~3,500 | ~1,000 | ~$0.007 |
| Heartbeat tick | 1 | ~2,000 | ~500 | ~$0.004 |
| Full e2e dispatch flow | 2 | ~7,000 | ~2,000 | ~$0.014 |
| **Total per full run** | **~10** | **~25,000** | **~6,500** | **~$0.046** |

**Conservative upper bound** (with LLM retries, classification variance):
**$0.05–$0.20 per full run**.

### Cost Tracking

The `cost_tracker` fixture accumulates actual token counts across all LLM calls
and prints a summary at session end:

```
════════════════════════════════════════════════════════════
E2E Cost Summary
  LLM calls:    10
  Input tokens:  24,312
  Output tokens:  6,847
  Est. cost:     $0.047
════════════════════════════════════════════════════════════
```

## Logging

### Log Capture

All application logs (from all 6 butler daemons) are captured at `DEBUG` level
to a timestamped log file:

```
.tmp/e2e-logs/
├── e2e-20260216-143022.log    # Timestamped per run
└── e2e-latest.log             # Symlink or tee output from make test-e2e
```

### Triage Commands

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

### Increasing Verbosity

```bash
# Maximum verbosity: all logs to console
uv run pytest tests/e2e/ -v -s --log-cli-level=DEBUG --tb=long
```

## Debugging

### Running a Single Scenario

```bash
# By scenario ID
uv run pytest tests/e2e/test_scenario_runner.py -v -k "health-weight-log" -s

# By test module
uv run pytest tests/e2e/test_health_flow.py -v -s --tb=long
```

### Interactive Debugging

The `butler_ecosystem` fixture is session-scoped. If you set a breakpoint in a
test, all 6 butlers remain running. You can manually call tools from the
debugger:

```python
from fastmcp import Client as MCPClient

async with MCPClient("http://localhost:8103/sse") as client:
    result = await client.call_tool("status", {})
    print(result)
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
│  │   :8100          :8101       :8102           :8103       │  │
│  │                                                          │  │
│  │  Messenger ──► Heartbeat                                 │  │
│  │   :8104        :8199                                     │  │
│  │                                                          │  │
│  │  Each: ButlerDaemon + FastMCP SSE + Spawner + DB pool   │  │
│  └──────────────────────┬──────────────────────────────────┘  │
│                         │                                      │
│                         ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │          TESTCONTAINER PostgreSQL (session-scoped)       │  │
│  │                                                          │  │
│  │  butler_switchboard  butler_general  butler_relationship │  │
│  │  butler_health       butler_messenger butler_heartbeat   │  │
│  │                                                          │  │
│  │  Core tables: state, scheduled_tasks, sessions           │  │
│  │  Butler tables: measurements, contacts, butler_registry  │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```
