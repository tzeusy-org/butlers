# Butler Lifecycle

> **Purpose:** Describe the full lifecycle of a butler daemon from startup through session execution to shutdown.
> **Audience:** Developers building modules, debugging butlers, or contributing to the framework.
> **Prerequisites:** [What Is Butlers?](../overview/what-is-butlers.md)

## Overview

![Butler Lifecycle](./butler-lifecycle.svg)

A butler daemon goes through distinct phases during its lifetime: a multi-step startup sequence that provisions infrastructure and initializes capabilities, an idle running state waiting for triggers, an active session state when an LLM is reasoning and acting, and a graceful shutdown sequence that drains work and releases resources. Understanding these phases is essential for debugging startup failures, writing modules, and reasoning about concurrency.

## Startup Sequence

The `ButlerDaemon.start()` method executes a carefully ordered startup sequence. Each step depends on the success of previous steps, though module-specific failures are non-fatal --- a failing module is recorded as failed and skipped in later phases while the butler continues with its remaining healthy modules.

### Step 1: Load Configuration and Logging

The daemon reads `butler.toml` from the config directory, parsing identity, database settings, schedules, runtime configuration, and module declarations. It then configures structured logging for that butler.

### Step 2: Initialize Telemetry and Scan Config

OpenTelemetry tracing and Prometheus-compatible metrics are initialized with a service name derived from the butler name (for example, `butler.general`). An inline secret scan warns about accidentally embedded credentials without blocking startup.

### Step 3: Initialize Modules (Topological Order)

The module registry instantiates modules in dependency order. The daemon skips a module only when its required `[modules.*]` configuration is omitted; optional-config modules may still start with defaults. A dependency cycle blocks startup.

### Step 4: Validate Module Configs

Each module declares a `config_schema` (a Pydantic model). The daemon validates the module-specific configuration from `butler.toml` against this schema. Validation failures are non-fatal: the module is marked failed with phase `config` and skipped in subsequent steps.

### Step 5: Validate Butler-Level Credentials

Environment variables declared in `[butler.env].required` and `[butler.env].optional` are checked. Missing required variables cause a hard startup failure. Module credentials are resolved later through the DB-backed credential store.

### Step 6: Provision Database and Set Butler Identity

The daemon creates or receives a connected `Database` and assigns `db.owner_butler` from the configured butler name. This identity is available before module startup, including for auditable schedule recovery. A schema-less legacy database continues to use its unqualified `public` schema.

### Step 7: Run Core and Butler Migrations

Core Alembic migrations run first (schema-scoped), followed by a butler-specific chain when one exists. This creates the state store, session log, scheduled tasks, and other core tables.

### Step 8: Prepare Module Dependencies and Bootstrap State

Module migration chains run, then the daemon creates a layered `CredentialStore`, validates module credentials, initializes optional blob storage, restores CLI auth, bootstraps owner/catalogue records, and recovers orphaned sessions. Module migration and credential failures remain non-fatal and cascade to dependents.

### Step 9: Resolve Runtime Config

The DB-backed runtime config is seeded from `[butler.runtime_seed]` on first boot and then becomes the source of truth for operational limits used by core-tool registration and the Spawner.

### Step 10: Sync Schedules

Scheduled tasks declared in `butler.toml` are synchronized to the database before any module starts. New tasks become TOML-owned, changed tasks update, and removed TOML tasks are disabled. This ordering lets a module-default registration observe a disabled TOML orphan while leaving DB-owned operator schedules untouched.

### Step 11: Module on_startup

Each healthy module's `on_startup()` method is called in topological order after schedule synchronization. This is where modules perform post-migration initialization, open connections, start background resources, load cached data, and register provenance-aware defaults. Failures are non-fatal and trigger cascade failures for dependent modules.

### Step 12: Create Spawner and Runtime Wiring

The daemon creates a `Spawner` with the configured runtime adapter (Claude Code, Codex, or Gemini), verifies the adapter binary is on `PATH`, configures audit/runtime wiring, and opens the Switchboard client connection.

### Step 13: Create FastMCP and Register Core Tools

A FastMCP server is created and core tools are registered: status, trigger, state operations, session queries, schedule management, notify, and remind.

### Step 14: Register Module Tools and Gates

Each healthy module registers its MCP tools. Approval gates and module runtime wiring are then applied; a module tool failure remains isolated to that module.

### Step 15: Start the FastMCP Server

The MCP SSE server starts listening on the configured port, followed by best-effort endpoint warm-up.

### Step 16: Start Recovery, Heartbeat, and Scheduler Services

The daemon launches route-inbox recovery, non-switchboard Switchboard heartbeats, and the internal scheduler loop. The scheduler calls `tick()` at the configured interval to dispatch due tasks.

### Step 17: Start Liveness Reporting

The liveness reporter starts periodic health pings and the daemon begins accepting connections.

## Running State

After startup completes, the butler enters its idle running state. It is:

- Listening for MCP connections on its configured port
- Running the scheduler loop (checking for due tasks every tick interval)
- Maintaining heartbeats with the Switchboard (non-switchboard butlers)
- Ready to accept `trigger` or `route.execute` calls

## Triggered State: The Session Cycle

When a trigger arrives (either from the `trigger` MCP tool, the `route.execute` dispatch from Switchboard, or the scheduler), the Spawner takes over:

1. **Concurrency gate** --- The spawner acquires a slot from both the per-butler semaphore (`max_concurrent_sessions`, default 1) and the global process-wide semaphore (`BUTLERS_MAX_GLOBAL_SESSIONS`, default 3). Self-trigger deadlocks are detected and rejected.

2. **Session creation** --- A database record is created with status `running`, capturing the trigger source, prompt, request ID, and timestamp.

3. **Model resolution** --- The model catalog is queried with the task complexity tier. If no catalog entry matches, the TOML-configured model is used as fallback.

4. **System prompt assembly** --- The base system prompt (from `CLAUDE.md`), owner routing instructions (from the database), and memory context (dynamically fetched based on the prompt) are composed into the final system prompt.

5. **Environment construction** --- A locked-down environment is built with only `PATH` (for shebang resolution), declared credentials, and trace propagation variables. Undeclared environment variables do not leak through.

6. **MCP config generation** --- A temporary MCP config is generated pointing exclusively at this butler's SSE endpoint. The LLM CLI will have no access to any other MCP servers.

7. **Runtime invocation** --- The adapter spawns the LLM CLI as a subprocess with the config, system prompt, prompt, and environment. The CLI connects to the butler's MCP server and begins reasoning through tool calls.

8. **Completion** --- When the CLI finishes, the spawner parses the output, extracts tool calls, records token usage, and updates the session record. If the memory module is enabled, the session output is stored as an episode. The semaphore slot is released.

## Shutdown Sequence

Graceful shutdown proceeds in reverse order:

1. Stop the MCP SSE server
2. Stop accepting new triggers
3. Drain in-flight runtime sessions (up to a configurable timeout)
4. Cancel the Switchboard heartbeat task
5. Close the Switchboard MCP client connection
6. Cancel the scheduler loop (waiting for any in-progress `tick()` to finish)
7. Cancel the liveness reporter loop
8. Shut down modules in **reverse** topological order (each module's `on_shutdown()`)
9. Close the database connection pool

## Module Failure Handling

Module failures during startup are handled with a cascade model. When a module fails at any phase (config, credentials, migration, startup, tools), it is recorded with a `ModuleStartupStatus` capturing the status (`failed`), phase, and error message. Any modules that declared a dependency on the failed module are automatically marked as `cascade_failed`. The butler continues operating with whatever modules remain healthy.

At runtime, module states can be queried via the `module.states` tool and modules can be toggled via `module.set_enabled`.

## Verification

To confirm the lifecycle described here matches the running system:

```bash
# 1. Startup log shows the expected phase sequence
# Start a butler and check its startup output for these phases:
#   "Loading config", "Initializing telemetry", "Running migrations",
#   "Registering tools", "FastMCP server starting"
butlers run --config roster/general 2>&1 | head -40

# 2. Module states are healthy after startup
# Call the butler's status MCP tool (via dashboard or MCP client):
#   Expected: all enabled modules show status "active"
curl -s http://localhost:41200/api/butlers/general/status | python3 -m json.tool

# 3. Triggered state: session created in DB
# After triggering the butler, check the sessions table:
curl -s http://localhost:41200/api/butlers/general/sessions | python3 -m json.tool
# Expected: a session with trigger_source "trigger", completed_at set

# 4. Shutdown drains in-flight sessions
# Send SIGTERM to the butler process; it should wait for active sessions to finish:
kill -TERM $(pgrep -f "butlers run --config roster/general")
# Expected: log lines for each shutdown step; no "active sessions dropped" errors

# 5. Module cascade failure model
# Temporarily break a module credential and restart; the butler should start
# with that module marked failed but all others healthy.
```

## Related Pages

- [Trigger Flow](trigger-flow.md) --- details on how triggers are sourced and dispatched
- [MCP Model](mcp-model.md) --- how MCP tools and the spawner interact
- [Modules and Connectors](modules-and-connectors.md) --- the module lifecycle in detail
