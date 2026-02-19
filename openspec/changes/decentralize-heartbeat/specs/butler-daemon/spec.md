# Butler Daemon Specification

Delta spec for decentralize-heartbeat change.

## MODIFIED Requirements

### Requirement: Core MCP Tool Registration

The daemon SHALL register core MCP tools on the FastMCP server instance. These tools provide the shared infrastructure every butler exposes. The following core tools MUST be registered:

- `status()` -- Returns butler identity (name, description), loaded modules, health status, and uptime.
- `tick()` -- Manually triggers the scheduler to check for due tasks. Primarily driven by the internal scheduler loop; exposed as an MCP tool for manual invocation and debugging. Returns a summary of executed tasks.
- `trigger(prompt, context?)` -- Spawns an ephemeral LLM CLI instance with the given prompt. Returns the runtime session result.
- `state_get(key)` -- Retrieves a JSONB value from the state store by key.
- `state_set(key, value)` -- Sets a JSONB value in the state store.
- `state_delete(key)` -- Deletes an entry from the state store by key.
- `state_list(prefix?)` -- Lists keys in the state store, optionally filtered by prefix.
- `schedule_list()` -- Lists all scheduled tasks.
- `schedule_create(name, cron, prompt)` -- Creates a new scheduled task. Returns the task ID.
- `schedule_update(id, ...)` -- Updates an existing scheduled task.
- `schedule_delete(id)` -- Deletes a scheduled task.
- `sessions_list(limit?, offset?)` -- Lists recent runtime sessions.
- `sessions_get(id)` -- Retrieves full detail of a runtime session including tool calls and outcome.

#### Scenario: All core tools registered at startup

WHEN the daemon completes module loading,
THEN the FastMCP server instance SHALL have all core tools registered,
AND each tool SHALL be callable via MCP protocol.

#### Scenario: Status tool returns butler identity

WHEN a client calls the `status()` tool on a running butler named "assistant" with modules `email` and `calendar`,
THEN the response SHALL include the butler name "assistant",
AND the response SHALL include the list of loaded module names,
AND the response SHALL include a health indicator,
AND the response SHALL include the daemon uptime.

#### Scenario: Core tools available on butler with no modules

WHEN the daemon starts with no modules configured,
THEN all core tools (status, tick, trigger, state_*, schedule_*, sessions_*) SHALL still be registered and callable.

---

### Requirement: Startup Sequence Ordering

The daemon MUST execute its startup sequence in the following order. Each step MUST complete successfully before the next step begins:

1. Load configuration from `butler.toml`.
2. Provision the PostgreSQL database (CREATE DATABASE IF NOT EXISTS).
3. Establish the database connection pool.
4. Apply core Alembic migrations (`alembic upgrade head` on core chain).
5. Apply butler-specific Alembic migrations (butler's version chain, if it exists).
6. Load and initialize modules in topological dependency order.
7. Register core MCP tools.
8. Register module MCP tools (via `register_tools()` on each module).
9. Start the FastMCP server on the configured port.
10. Start the internal scheduler loop (asyncio background task).
11. Start the liveness reporter (asyncio background task, unless this butler is the Switchboard).

If any step in 1–9 fails, the daemon MUST halt startup and report the error. Steps after the failing step MUST NOT execute. Steps 10–11 are post-server-ready tasks that run as background asyncio tasks.

#### Scenario: Full startup sequence succeeds

WHEN the daemon starts with a valid config, accessible database, clean Alembic state, and valid modules,
THEN the daemon SHALL complete all eleven steps in order,
AND the FastMCP server SHALL be listening and ready to accept connections,
AND the internal scheduler loop SHALL be running,
AND the liveness reporter SHALL be running (unless the butler is the Switchboard).

#### Scenario: Alembic migration failure prevents tool registration

WHEN the daemon fails during step 4 (core Alembic migrations),
THEN steps 5 through 11 SHALL NOT execute,
AND the daemon SHALL report the Alembic migration error.

#### Scenario: Module loading failure prevents server startup

WHEN the daemon fails during step 6 (a module's `on_startup()` raises an exception),
THEN steps 7 through 11 SHALL NOT execute,
AND the daemon SHALL report the module initialization error,
AND any already-initialized modules SHALL have their `on_shutdown()` called.

---

### Requirement: Graceful Shutdown

The daemon SHALL support graceful shutdown. When a shutdown signal is received (e.g., SIGTERM, SIGINT), the daemon MUST:

1. Stop accepting new MCP connections.
2. Cancel the internal scheduler loop and liveness reporter asyncio tasks. If a `tick()` call is in progress, wait for it to complete.
3. Wait for any in-flight Claude Code sessions to complete (up to a configurable timeout).
4. Call `on_shutdown()` on each loaded module in reverse initialization order.
5. Close the database connection pool.
6. Exit cleanly.

The daemon MUST NOT terminate in-flight runtime sessions abruptly unless the shutdown timeout is exceeded.

#### Scenario: Clean shutdown with no active sessions

WHEN the daemon receives SIGTERM and no runtime sessions are in progress,
THEN the daemon SHALL stop accepting new connections,
AND cancel the scheduler loop and liveness reporter,
AND call `on_shutdown()` on all modules in reverse order,
AND close the database connection pool,
AND exit with status code 0.

#### Scenario: Shutdown waits for in-flight runtime session

WHEN the daemon receives SIGTERM while a runtime session is actively running,
THEN the daemon SHALL stop accepting new MCP connections,
AND cancel the scheduler loop and liveness reporter,
AND the daemon SHALL wait for the in-flight runtime session to complete before calling module `on_shutdown()`,
AND after the session completes, the daemon SHALL proceed with the remaining shutdown steps.

#### Scenario: Shutdown timeout forces exit

WHEN the daemon receives SIGTERM while a runtime session is running, and the session does not complete within the shutdown timeout,
THEN the daemon SHALL log a warning about the timed-out session,
AND proceed with module shutdown and database pool closure,
AND exit.

#### Scenario: Background tasks cancelled before module shutdown

WHEN the daemon shuts down and modules A, B, C were initialized in that order,
THEN the scheduler loop and liveness reporter MUST be cancelled first,
THEN `on_shutdown()` SHALL be called on C, then B, then A.
