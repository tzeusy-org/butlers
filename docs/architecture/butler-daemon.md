# Butler Daemon Internals

> **Purpose:** Describes the internal architecture of the butler daemon — the central orchestrator for every butler instance.
> **Audience:** Developers extending butlers, operators debugging startup failures, architects understanding the lifecycle.
> **Prerequisites:** [System Topology](system-topology.md), [Database Design](database-design.md).

## Overview


![Daemon Startup Sequence](./startup-sequence.svg)
Every butler in the system is a long-running Python process managed by the `ButlerDaemon` class (`src/butlers/daemon.py`). The daemon owns the full lifecycle of a butler: loading configuration, provisioning infrastructure, wiring modules, and serving MCP tools over an SSE transport. It is the single entry point for both `butlers run --config` (single butler) and `butlers up` (multi-butler) execution modes.

## Startup Sequence

The daemon follows a deterministic multi-phase startup. Each phase depends on the successful completion of the previous one. If any phase fails, already-initialized modules receive their `on_shutdown()` call before the process exits.

### Phase 1: Load and Validate Config

The daemon reads `butler.toml` from the config directory, parses it into a `ButlerConfig`, and configures structured logging. Required fields (`name`, `port`) are validated. Environment variable references (`${VAR_NAME}`) in config values are resolved; unresolved required references are startup-blocking errors.

### Phase 2: Initialize Telemetry and Scan Config

`init_telemetry(service_name)` and `init_metrics(service_name)` set up the global OpenTelemetry `TracerProvider` and `MeterProvider`. When `OTEL_EXPORTER_OTLP_ENDPOINT` is not set, both fall back to no-op providers. The daemon also scans flattened configuration values for inline secrets and emits warnings without blocking startup.

### Phase 3: Initialize Modules (Topological Order)

The `ModuleRegistry` instantiates modules in dependency order. The daemon skips a module only when its required configuration is omitted from `[modules.*]`; optional-config modules may still start with defaults. Dependency cycles are startup-blocking errors.

### Phase 4: Validate Module Configs

Each module's `config_schema` (a Pydantic model) is validated against its TOML section. A validation failure marks only that module unavailable and cascade-fails its dependents.

### Phase 5: Validate Butler Credentials

Butler-level `[butler.env].required` and `[butler.env].optional` variables are checked before database work. Missing required variables block startup; module credentials are resolved later through the database-backed credential store.

### Phase 6: Provision Database and Establish Butler Identity

The daemon provisions and connects a `Database` unless an already-connected instance was injected. It assigns `db.owner_butler` from the configured butler name before any module can make an auditable recovery. In the one-db/multi-schema topology, all butlers share `butlers` and use a schema-scoped search path; legacy per-butler databases retain the unqualified `public` schema.

### Phase 7: Run Core and Butler Migrations

Alembic runs the core chain and, where present, the butler-specific chain. These migrations create the mandatory state, schedule, session, and process-log tables.

### Phase 8: Prepare Module Dependencies and Bootstrap State

Module migration chains run next. The daemon builds a DB-first `CredentialStore`, validates module credentials, initializes optional blob storage, restores CLI auth, bootstraps owner/catalogue records, and recovers orphaned sessions. Module migration and credential failures remain isolated to the affected module.

### Phase 9: Resolve Runtime Config

The daemon seeds and reads the DB-backed runtime configuration from `[butler.runtime_seed]` when necessary. The resulting operational limits are the source of truth for core-tool registration and the Spawner.

### Phase 10: Sync TOML Schedules

`sync_schedules()` reads `[[butler.schedule]]` entries from `butler.toml` and upserts them into `scheduled_tasks`. New tasks are TOML-owned; changed tasks are updated; removed TOML tasks are disabled. This phase deliberately runs **before module `on_startup()`** so a module-default registration sees the post-sync provenance state. That makes a removed TOML default observable as a disabled TOML row while preserving DB-owned operator schedules.

### Phase 11: Start Modules

Each healthy module's `on_startup(config, db)` runs in topological order after schedule synchronization. Modules can now initialize external connections, caches, background resources, and provenance-aware default schedules. Startup failures are non-fatal per module and cascade to dependents.

### Phase 12: Create Spawner and Runtime Wiring

The daemon creates a `Spawner` with the runtime adapter, verified binary, connection pool, and module credentials. It then establishes daemon-side audit/runtime wiring, configures the switchboard pipeline where applicable, and opens the Switchboard client connection.

### Phase 13: Create FastMCP and Register Core Tools

A `FastMCP` server is created and core MCP tools are registered: `status`, `trigger`, `route.execute`, `tick`, state tools (`state_get`, `state_set`, `state_delete`, `state_list`), schedule tools, session tools, `notify`, and `remind`.

### Phase 14: Register Module Tools and Gates

Healthy modules register tools through `register_tools(mcp, config, db)`. Approval gates and module-runtime wiring are then applied; a module-tool failure remains isolated to that module.

### Phase 15: Start the FastMCP Server

The FastMCP SSE server starts on the configured port via uvicorn. Endpoint warm-up is launched best-effort after the server is listening.

### Phase 16: Start Recovery, Heartbeat, and Scheduler Services

The daemon launches route-inbox recovery, the non-switchboard Switchboard heartbeat, and the internal scheduler loop. The scheduler calls `tick()` at its configured interval (default 60 seconds).

### Phase 17: Start Liveness Reporting

Finally, the liveness reporter begins periodic health pings and the daemon marks itself ready to accept connections.

## Core Components

### State Store

A key-value store backed by the `state` table (JSONB values). Provides `state_get`, `state_set`, `state_delete`, and `state_list` operations. Used by both the daemon itself (e.g., tracking disambiguation notifications) and by LLM runtime instances through MCP tools.

### Scheduler

Cron-driven task dispatch. The scheduler maintains a `scheduled_tasks` table with cron expressions evaluated by `croniter`. On each `tick()`, due tasks are dispatched through the spawner. Tasks support two dispatch modes: `prompt` (sends text to the LLM CLI) and `job` (sends structured job name + arguments). See [Scheduler Execution](../runtime/scheduler-execution.md) for runtime behavior details.

### Session Log

An append-only record of LLM CLI invocations. Each session row is created before the runtime is invoked and completed when it returns. Fields include prompt, trigger source, model, duration, token counts, tool calls, and outcome. The only mutation after creation is `session_complete`. See [Session Lifecycle](../runtime/session-lifecycle.md).

### Spawner

The component that invokes ephemeral AI runtime instances. Controlled by an `asyncio.Semaphore` for per-butler concurrency limiting (default 1 = serial dispatch) and a process-wide global semaphore (default 3 max concurrent sessions across all butlers). See [Spawner](../runtime/spawner.md).

## Module Loading

Modules implement the `Module` abstract base class. The loading process:

1. The `ModuleRegistry` maps module names to their implementation classes.
2. The daemon instantiates registered modules; `[modules.*]` sections provide explicit configuration, while only modules with omitted required config are skipped.
3. Dependencies declared by each module (via the `dependencies` property) are resolved into a topological order using a deterministic sort.
4. Circular dependencies are detected and reported as startup errors.
5. Modules are initialized, started, and their tools registered in dependency order.
6. On shutdown, modules are torn down in reverse topological order.

Each module has a well-defined lifecycle: `config_schema` validation, `migration_revisions()` for DB setup, `on_startup()` for initialization, `register_tools()` for MCP tool registration, and `on_shutdown()` for cleanup.

## Graceful Shutdown

The shutdown sequence is the inverse of startup:

1. Stop the MCP server (stop accepting new connections).
2. Stop accepting new triggers on the spawner.
3. Drain in-flight runtime sessions within the configurable timeout (`[butler.shutdown].timeout_s`).
4. Cancel the switchboard heartbeat task.
5. Close the Switchboard MCP client connection.
6. Cancel the scheduler loop (waits for any in-progress `tick()` to complete).
7. Cancel the liveness reporter loop.
8. Shut down modules in reverse topological order via `on_shutdown()`.
9. Close the database connection pool.

## Verification

To confirm the daemon startup sequence and core components match the running system:

```bash
# 1. All startup phases complete without error
# Start a butler and check for the expected phase sequence in log output
butlers run --config roster/general 2>&1 | grep -E "Phase|Loading|migration|FastMCP|startup"
# Expected: phase markers appear in order; no "startup-blocking error" lines

# 2. Core tables exist in the butler's schema after startup
psql -h localhost -U butlers -d butlers -c \
  "SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'general'
   ORDER BY table_name;"
# Expected: state, scheduled_tasks, sessions, session_process_logs present

# 3. Schedules sync from TOML on startup
psql -h localhost -U butlers -d butlers -c \
  "SELECT name, cron, source, enabled, next_run_at FROM general.scheduled_tasks ORDER BY name;"
# Expected: tasks listed in butler.toml appear with source='toml'

# 4. Spawner concurrency limit is respected
curl -s http://localhost:41200/api/butlers/general/status | python3 -m json.tool
# Expected: active_sessions field; check it never exceeds the configured max_concurrent

# 5. Graceful shutdown drains in-flight sessions
kill -TERM $(pgrep -f "butlers run --config roster/general")
# Expected: log shows each shutdown step; process exits cleanly (exit code 0)
# No "active sessions dropped" error lines should appear
```

## Related Pages

- [System Topology](system-topology.md) — how butlers fit into the overall service architecture
- [Database Design](database-design.md) — schema isolation and migration strategy
- [Observability](observability.md) — telemetry initialization details
- [Spawner](../runtime/spawner.md) — the runtime invocation component
- [Scheduler Execution](../runtime/scheduler-execution.md) — cron-driven task dispatch behavior
