# Butler Daemon Specification

The butler daemon is the foundational runtime of the Butlers framework. Each butler is a long-running FastMCP server daemon that loads configuration, provisions its database, initializes modules, registers MCP tools, and serves requests until shut down. This spec defines the lifecycle, configuration, database provisioning, module loading, tool registration, and shutdown behavior that every butler daemon MUST implement.

---

## ADDED Requirements

### Requirement: Config Loading

The daemon SHALL load its configuration from a `butler.toml` file in TOML format located in the butler's config directory. The config directory MUST also support `CLAUDE.md`, `AGENTS.md`, and a `skills/` subdirectory, though only `butler.toml` is required for daemon startup.

The `butler.toml` file MUST support the following sections:

- `[butler]` -- Butler identity. MUST contain `name` (string) and `port` (integer). MAY contain `description` (string).
- `[butler.db]` -- Database configuration. MAY contain `name` (string). If omitted, the database name SHALL default to `butler_<name>` where `<name>` is the butler's name from the `[butler]` section.
- `[[butler.schedule]]` -- Zero or more scheduled task definitions. Each entry MUST contain `name` (string), `cron` (string, cron expression), and `prompt` (string).
- `[modules.*]` -- Zero or more module configuration sections. Each `[modules.<module_name>]` section enables that module and provides its module-specific configuration as key-value pairs.

The daemon MUST reject startup with a clear error if `butler.toml` is missing, unparseable, or lacks the required `[butler]` section with `name` and `port` fields.

#### Scenario: Valid full config loads successfully

WHEN the daemon starts with a `butler.toml` containing `[butler]` with name, description, and port, `[butler.db]` with name, two `[[butler.schedule]]` entries, and `[modules.email]` and `[modules.calendar]` sections,
THEN the daemon SHALL parse all sections without error,
AND the butler name, description, port, database name, schedule entries, and module configs SHALL all be accessible on the resulting config object.

#### Scenario: Minimal config loads with defaults

WHEN the daemon starts with a `butler.toml` containing only `[butler]` with `name = "minimal"` and `port = 8100`,
THEN the daemon SHALL load successfully,
AND the database name SHALL default to `butler_minimal`,
AND the schedule list SHALL be empty,
AND the module list SHALL be empty.

#### Scenario: Missing butler.toml prevents startup

WHEN the daemon starts and no `butler.toml` file exists in the config directory,
THEN the daemon SHALL raise a configuration error and MUST NOT proceed to database provisioning or server startup.

#### Scenario: Invalid TOML prevents startup

WHEN the daemon starts with a `butler.toml` that contains malformed TOML syntax,
THEN the daemon SHALL raise a parse error with a message indicating the nature of the syntax problem.

#### Scenario: Missing required fields prevent startup

WHEN the daemon starts with a `butler.toml` that has a `[butler]` section but is missing the `name` field,
THEN the daemon SHALL raise a validation error indicating that `name` is required,
AND the daemon MUST NOT proceed to database provisioning or server startup.

#### Scenario: Missing port prevents startup

WHEN the daemon starts with a `butler.toml` that has a `[butler]` section with `name` but no `port`,
THEN the daemon SHALL raise a validation error indicating that `port` is required.

---

### Requirement: Database Provisioning

On startup, the daemon SHALL provision its PostgreSQL database. The daemon MUST connect to the PostgreSQL server and create the butler's database if it does not already exist. The database name is determined by `[butler.db].name` in config, or defaults to `butler_<name>`.

The daemon SHALL use `asyncpg` for all database access. No ORM SHALL be used. The daemon MUST establish a connection pool to the butler's database after provisioning.

#### Scenario: Database is created on first startup

WHEN the daemon starts for the first time and the database `butler_assistant` does not exist on the PostgreSQL server,
THEN the daemon SHALL execute `CREATE DATABASE butler_assistant`,
AND the daemon SHALL establish a connection pool to the newly created database.

#### Scenario: Database already exists on subsequent startup

WHEN the daemon starts and the database `butler_assistant` already exists on the PostgreSQL server,
THEN the daemon SHALL NOT attempt to create the database,
AND the daemon SHALL establish a connection pool to the existing database.

#### Scenario: PostgreSQL connection failure prevents startup

WHEN the daemon starts but cannot connect to the PostgreSQL server,
THEN the daemon SHALL raise a connection error and MUST NOT proceed to Alembic migration or server startup.

---

### Requirement: Core Migration Application

After database provisioning, the daemon SHALL apply core database migrations using Alembic. Core migrations create the shared tables that every butler requires: `state`, `scheduled_tasks`, and `sessions`.

Migrations SHALL be Alembic revisions in the `core` version chain under `alembic/versions/core/`. The daemon SHALL run `alembic upgrade head` programmatically (via `alembic.command.upgrade`) against the butler's database to apply pending revisions. Alembic's `alembic_version` table tracks which revisions have been applied. A revision that has already been applied SHALL NOT be re-applied.

#### Scenario: Core tables created on first startup

WHEN the daemon starts against an empty database with no `alembic_version` table,
THEN the daemon SHALL initialize Alembic version tracking,
AND the daemon SHALL apply all pending revisions from the `core` Alembic chain,
AND after migration the database SHALL contain the `state`, `scheduled_tasks`, and `sessions` tables,
AND each applied revision SHALL be recorded in the `alembic_version` table.

#### Scenario: Already-applied migrations are skipped

WHEN the daemon starts and the `alembic_version` table already records the core chain as up-to-date,
THEN the daemon SHALL NOT re-apply any revisions,
AND the daemon SHALL apply only newer revisions not yet recorded.

#### Scenario: Migration failure halts startup

WHEN a core Alembic revision contains a syntax error or fails to execute,
THEN the daemon SHALL raise an error and MUST NOT proceed to module loading or server startup,
AND the failed revision SHALL NOT be recorded in `alembic_version`.

---

### Requirement: Butler-Specific Migration Application

After core migrations, the daemon SHALL apply butler-specific Alembic migrations if a version chain exists for the butler (e.g., `alembic/versions/relationship/`). These migrations create tables specific to the butler's domain (e.g., `contacts` for the Relationship butler, `measurements` for the Health butler).

Butler-specific migrations follow the same Alembic mechanism as core migrations: revisions tracked via `alembic_version`, applied via `alembic upgrade head` on the butler's branch, idempotent on re-run.

#### Scenario: Butler-specific migrations applied after core

WHEN the daemon starts for the Relationship butler and the `relationship` Alembic version chain has pending revisions,
THEN the daemon SHALL first apply all core Alembic revisions,
AND THEN apply the `relationship` chain revisions,
AND both core and butler-specific revisions SHALL be tracked by Alembic.

#### Scenario: No butler-specific Alembic chain

WHEN the daemon starts for a butler named "minimal" and no `alembic/versions/minimal/` directory exists,
THEN the daemon SHALL skip butler-specific migrations without error,
AND core migrations SHALL still be applied.

---

### Requirement: Module Loading and Initialization

After migrations, the daemon SHALL load and initialize modules declared in `[modules.*]` sections of `butler.toml`. Modules MUST be loaded in topological order based on their declared `dependencies` to ensure that any module a given module depends on is initialized first.

For each module, the daemon SHALL:

1. Resolve the module class from the module name.
2. Validate the module's configuration against its `config_schema`.
3. Call `migration_revisions()` and apply any module-specific Alembic revisions.
4. Call `on_startup(config, db)` to initialize the module.

If a module declares a dependency that is not present in the butler's config, the daemon MUST raise an error at startup. If modules form a circular dependency, the daemon MUST detect this and raise an error.

#### Scenario: Single module loads and initializes

WHEN the daemon starts with `[modules.email]` in config and the email module has no dependencies,
THEN the daemon SHALL load the email module,
AND apply the email module's Alembic revisions,
AND call `on_startup()` on the email module,
AND the email module SHALL be available for tool registration.

#### Scenario: Modules loaded in dependency order

WHEN the daemon starts with `[modules.calendar]` and `[modules.email]` in config, and the calendar module declares a dependency on email,
THEN the daemon SHALL initialize the email module before the calendar module.

#### Scenario: Missing dependency prevents startup

WHEN the daemon starts with `[modules.calendar]` in config, and the calendar module declares a dependency on `email`, but `[modules.email]` is not present in config,
THEN the daemon SHALL raise an error indicating that the `calendar` module requires `email`.

#### Scenario: Circular dependency detected

WHEN the daemon starts with modules A and B, where A depends on B and B depends on A,
THEN the daemon SHALL raise an error indicating a circular dependency and MUST NOT proceed to server startup.

#### Scenario: No modules configured

WHEN the daemon starts with no `[modules.*]` sections in config,
THEN the daemon SHALL skip module loading without error,
AND the butler SHALL operate with only core MCP tools.

---

### Requirement: Core MCP Tool Registration

The daemon SHALL register core MCP tools on the FastMCP server instance. These tools provide the shared infrastructure every butler exposes. The following core tools MUST be registered:

- `status()` -- Returns butler identity (name, description), loaded modules, health status, and uptime.
- `tick()` -- Entry point for the heartbeat. Triggers the scheduler to check for due tasks. Returns a summary of executed tasks.
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

### Requirement: Module MCP Tool Registration

After core tools are registered, the daemon SHALL call `register_tools(mcp, config, db)` on each loaded module to allow it to register its domain-specific MCP tools on the same FastMCP server instance.

Module tools SHALL be registered on the same MCP server as core tools. Clients connecting to a butler's MCP endpoint SHALL see both core and module tools.

#### Scenario: Module tools appear alongside core tools

WHEN the daemon starts with the email module, and the email module registers `bot_email_send_message`, `bot_email_search_inbox`, and `bot_email_read_message` tools,
THEN the MCP server SHALL expose all core tools plus `bot_email_send_message`, `bot_email_search_inbox`, and `bot_email_read_message`,
AND a client connecting to this butler SHALL be able to call any of these tools.

#### Scenario: Multiple modules register tools without conflict

WHEN the daemon starts with both email and calendar modules, and each registers its own tools,
THEN all tools from both modules SHALL be registered on the MCP server alongside core tools,
AND there SHALL be no name collisions between module tools.

---

### Requirement: FastMCP Server Startup

After all tools are registered (core + module), the daemon SHALL start the FastMCP server on the port specified in `[butler].port`. The server SHALL use SSE (Server-Sent Events) transport for MCP communication.

The server MUST be fully operational before the daemon signals readiness. The daemon SHALL log a startup message indicating the butler name and the port it is listening on.

#### Scenario: Server starts on configured port

WHEN the daemon has completed all initialization steps and the config specifies `port = 8101`,
THEN the FastMCP server SHALL begin accepting MCP connections on port 8101 via SSE transport.

#### Scenario: Port conflict prevents startup

WHEN the daemon attempts to start the FastMCP server on a port that is already in use,
THEN the daemon SHALL raise an error indicating the port conflict and MUST NOT silently fail.

#### Scenario: Startup log message emitted

WHEN the FastMCP server successfully starts,
THEN the daemon SHALL log a message containing the butler name and the listening port.

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

If any step fails, the daemon MUST halt startup and report the error. Steps after the failing step MUST NOT execute.

#### Scenario: Full startup sequence succeeds

WHEN the daemon starts with a valid config, accessible database, clean Alembic state, and valid modules,
THEN the daemon SHALL complete all nine steps in order,
AND the FastMCP server SHALL be listening and ready to accept connections.

#### Scenario: Alembic migration failure prevents tool registration

WHEN the daemon fails during step 4 (core Alembic migrations),
THEN steps 5 through 9 SHALL NOT execute,
AND the daemon SHALL report the Alembic migration error.

#### Scenario: Module loading failure prevents server startup

WHEN the daemon fails during step 6 (a module's `on_startup()` raises an exception),
THEN steps 7 through 9 SHALL NOT execute,
AND the daemon SHALL report the module initialization error,
AND any already-initialized modules SHALL have their `on_shutdown()` called.

---

### Requirement: Graceful Shutdown

The daemon SHALL support graceful shutdown. When a shutdown signal is received (e.g., SIGTERM, SIGINT), the daemon MUST:

1. Stop accepting new MCP connections.
2. Wait for any in-flight Claude Code sessions to complete (up to a configurable timeout).
3. Call `on_shutdown()` on each loaded module in reverse initialization order.
4. Close the database connection pool.
5. Exit cleanly.

The daemon MUST NOT terminate in-flight runtime sessions abruptly unless the shutdown timeout is exceeded.

#### Scenario: Clean shutdown with no active sessions

WHEN the daemon receives SIGTERM and no runtime sessions are in progress,
THEN the daemon SHALL stop accepting new connections,
AND call `on_shutdown()` on all modules in reverse order,
AND close the database connection pool,
AND exit with status code 0.

#### Scenario: Shutdown waits for in-flight runtime session

WHEN the daemon receives SIGTERM while a runtime session is actively running,
THEN the daemon SHALL stop accepting new MCP connections,
AND the daemon SHALL wait for the in-flight runtime session to complete before calling module `on_shutdown()`,
AND after the session completes, the daemon SHALL proceed with the remaining shutdown steps.

#### Scenario: Shutdown timeout forces exit

WHEN the daemon receives SIGTERM while a runtime session is running, and the session does not complete within the shutdown timeout,
THEN the daemon SHALL log a warning about the timed-out session,
AND proceed with module shutdown and database pool closure,
AND exit.

#### Scenario: Module shutdown called in reverse order

WHEN the daemon shuts down and modules A, B, C were initialized in that order,
THEN `on_shutdown()` SHALL be called on C first, then B, then A.

---

### Requirement: Butler Class as Composition Root

The `Butler` class SHALL serve as the composition root that owns and wires together all components of a butler daemon. A single `Butler` instance SHALL hold:

- The parsed configuration (`ButlerConfig`).
- The database connection pool.
- Core component instances (state store, scheduler, LLM CLI spawner, session log).
- Loaded and initialized module instances.
- The FastMCP server instance.

The `Butler` class SHALL expose methods for the full lifecycle: `start()` to execute the startup sequence and `stop()` to execute graceful shutdown.

#### Scenario: Butler instance owns all components

WHEN a `Butler` instance is created with a valid config and started,
THEN it SHALL hold references to the database pool, state store, scheduler, spawner, session log, loaded modules, and FastMCP server,
AND all components SHALL be accessible for the duration of the butler's lifetime.

#### Scenario: Butler start executes full startup sequence

WHEN `butler.start()` is called,
THEN the Butler SHALL execute the full startup sequence (config already loaded, provision DB, apply Alembic migrations, load modules, register tools, start server) in the specified order.

#### Scenario: Butler stop executes graceful shutdown

WHEN `butler.stop()` is called,
THEN the Butler SHALL execute the graceful shutdown sequence (stop connections, wait for CC, shutdown modules, close DB pool).

---

### Requirement: TOML Schedule Sync on Startup

During startup, after Alembic migrations are applied and before the server starts, the daemon SHALL synchronize scheduled tasks defined in `[[butler.schedule]]` sections of `butler.toml` to the `scheduled_tasks` database table.

Tasks from TOML SHALL have their `source` field set to `'toml'`. If a TOML-defined task already exists in the database (matched by `name`), the daemon SHALL update its `cron` and `prompt` fields to match the TOML definition. If a TOML-defined task does not exist in the database, the daemon SHALL insert it. Tasks with `source = 'db'` (runtime-created) SHALL NOT be affected by TOML sync.

#### Scenario: TOML tasks inserted on first startup

WHEN the daemon starts with two `[[butler.schedule]]` entries and the `scheduled_tasks` table is empty,
THEN the daemon SHALL insert both tasks into `scheduled_tasks` with `source = 'toml'`,
AND each task SHALL have its `next_run_at` computed from its cron expression.

#### Scenario: TOML task updated on config change

WHEN the daemon starts and a `[[butler.schedule]]` entry has `name = "morning-briefing"` with a new cron expression, and a task named "morning-briefing" with `source = 'toml'` already exists in the database,
THEN the daemon SHALL update the existing task's `cron` and `prompt` fields to match the TOML definition,
AND the `next_run_at` SHALL be recomputed.

#### Scenario: Runtime-created tasks preserved during sync

WHEN the daemon starts and the `scheduled_tasks` table contains a task with `source = 'db'` that was created at runtime via `schedule_create`,
THEN the TOML sync SHALL NOT modify or delete that task.

#### Scenario: No schedule in TOML

WHEN the daemon starts with no `[[butler.schedule]]` entries in `butler.toml`,
THEN the daemon SHALL not insert any TOML-sourced tasks,
AND any existing `source = 'db'` tasks in the database SHALL be preserved.

---

### Requirement: Health and Uptime Tracking

The daemon SHALL track its own startup time and compute uptime on demand. The `status()` tool MUST report accurate uptime reflecting how long the daemon has been running since the FastMCP server became ready.

#### Scenario: Uptime increases over time

WHEN the daemon has been running for 300 seconds and a client calls `status()`,
THEN the response SHALL include an uptime value of approximately 300 seconds.

#### Scenario: Health status reflects component state

WHEN the daemon is fully started with database pool active and all modules initialized,
THEN the `status()` tool SHALL report a healthy status,
AND if the database pool becomes unavailable, the status SHALL reflect the degraded state.

---

### Requirement: Logging

The daemon SHALL log significant lifecycle events using structured logging. At minimum, the following events MUST be logged:

- Configuration loaded (butler name, port).
- Database provisioned or connected.
- Each Alembic migration revision applied.
- Each module loaded and initialized.
- FastMCP server started (port).
- Shutdown initiated.
- Each module shutdown completed.
- Database pool closed.

#### Scenario: Startup events logged

WHEN the daemon starts successfully,
THEN the log output SHALL contain entries for config loaded, database connected, Alembic migrations applied, modules initialized, and server started.

#### Scenario: Shutdown events logged

WHEN the daemon shuts down,
THEN the log output SHALL contain entries for shutdown initiated, each module shutdown, and database pool closed.
