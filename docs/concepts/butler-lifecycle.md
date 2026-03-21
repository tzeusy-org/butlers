# Butler Lifecycle

> **Purpose:** Describe the full lifecycle of a butler daemon from startup through session execution to shutdown.
> **Audience:** Developers building modules, debugging butlers, or contributing to the framework.
> **Prerequisites:** [What Is Butlers?](../overview/what-is-butlers.md)

## Overview

A butler daemon goes through distinct phases during its lifetime: a multi-step startup sequence that provisions infrastructure and initializes capabilities, an idle running state waiting for triggers, an active session state when an LLM is reasoning and acting, and a graceful shutdown sequence that drains work and releases resources. Understanding these phases is essential for debugging startup failures, writing modules, and reasoning about concurrency.

## Startup Sequence

The `ButlerDaemon.start()` method executes a carefully ordered startup sequence. Each step depends on the success of previous steps, though module-specific failures are non-fatal --- a failing module is recorded as failed and skipped in later phases while the butler continues with its remaining healthy modules.

### Step 1: Load Configuration

The daemon reads `butler.toml` from the config directory, parsing identity (name, port, description), database settings, schedules, runtime configuration, and module declarations. Structured logging is then configured based on the butler's logging settings.

### Step 1c: Initialize Blob Storage

A local blob store is set up for file attachments at the configured storage path.

### Step 2: Initialize Telemetry and Metrics

OpenTelemetry tracing and Prometheus-compatible metrics are initialized with a service name derived from the butler name (e.g., `butler.general`). An inline secret scan runs against the flattened config values to detect accidentally embedded credentials.

### Step 3: Initialize Modules (Topological Order)

The module registry instantiates all built-in modules, then the daemon filters down to those that are declared in `[modules.*]` config sections. Modules declare dependencies on other modules, so initialization follows a topological sort --- a module is only initialized after all its dependencies.

### Step 4: Validate Module Configs

Each module declares a `config_schema` (a Pydantic model). The daemon validates the module-specific configuration from `butler.toml` against this schema. Validation failures are non-fatal: the module is marked as failed with phase `config` and skipped in subsequent steps.

### Step 5: Validate Butler-Level Credentials

Environment variables declared in `[butler.env].required` and `[butler.env].optional` are checked. Missing required variables cause a hard startup failure. Missing optional variables produce warnings.

### Step 6: Provision Database

The daemon creates a `Database` instance from environment variables, sets the butler's schema, provisions the database (creating the database and schema if they do not exist), and opens a connection pool.

### Step 7: Run Migrations

Core Alembic migrations run first (schema-scoped), followed by butler-specific migrations if the butler has its own migration chain. This creates the state store, session log, scheduled tasks, and other core tables.

### Step 8: Run Module Migrations

Each module that declares `migration_revisions()` gets its Alembic chain run. Module migration failures are non-fatal --- the module is marked failed with phase `migration` and its dependents are cascade-failed.

### Step 8b: Credential Store and Module Credential Validation

A layered `CredentialStore` is created with access to the database pool, enabling DB-first credential resolution with environment variable fallback. Module credentials are validated asynchronously against this store. Missing credentials mark the module as failed.

### Step 8d: Bootstrap Owner Entity

An idempotent operation ensures the owner entity exists in `shared.entities`. This is the identity anchor for the system's user.

### Step 9: Module on_startup

Each healthy module's `on_startup()` method is called in topological order. This is where modules perform post-migration initialization: opening connections, starting background tasks, loading cached data. Failures are non-fatal and trigger cascade failures for dependent modules.

### Step 10: Create Spawner

The daemon creates a `Spawner` instance with the configured runtime adapter (Claude Code, Codex, or Gemini). The adapter's binary is verified to be on `PATH`. If the binary is missing, startup fails with a `RuntimeBinaryNotFoundError`.

### Step 11: Sync Schedules

Scheduled tasks declared in `butler.toml` are synchronized to the database, creating or updating schedule records. This ensures the scheduler has an accurate view of what to run and when.

### Step 12--14: MCP Server and Tool Registration

A FastMCP server is created and core tools are registered (status, trigger, state operations, session queries, schedule management, notify, remind). Then module tools are registered for each healthy module. Approval gates are applied to configured gated tools. Finally, the MCP SSE server starts listening on the butler's configured port.

### Step 15--17: Background Services

Non-switchboard butlers open an MCP client connection to the Switchboard for inter-butler communication and start a liveness reporter that periodically POSTs heartbeats. The internal scheduler loop begins calling `tick()` at the configured interval, which checks for due scheduled tasks and fires them.

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

## Related Pages

- [Trigger Flow](trigger-flow.md) --- details on how triggers are sourced and dispatched
- [MCP Model](mcp-model.md) --- how MCP tools and the spawner interact
- [Modules and Connectors](modules-and-connectors.md) --- the module lifecycle in detail
