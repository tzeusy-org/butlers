# Butler Daemon Lifecycle

## Purpose
Defines the startup, runtime, and shutdown lifecycle of a single butler daemon instance, including configuration loading, phased initialization, health/status reporting, and graceful shutdown with bounded drain.

## ADDED Requirements

### Requirement: Configuration Loading and Validation
The daemon loads `butler.toml` from a git-backed config directory, resolves `${VAR_NAME}` environment variable references recursively across strings/dicts/lists, and produces a validated `ButlerConfig` dataclass. Unresolved required env vars are startup-blocking `ConfigError` exceptions. The config includes required fields (`butler.name`, `butler.port`), optional sub-sections (`butler.db`, `butler.env`, `butler.runtime`, `butler.scheduler`, `butler.shutdown`, `butler.logging`, `butler.switchboard`, `butler.security`, `butler.storage`, `buffer`), schedule entries (`[[butler.schedule]]`), and module declarations (`[modules.*]`).

#### Scenario: Valid config loads successfully
- **WHEN** a valid `butler.toml` exists in the config directory with required fields `butler.name` and `butler.port`
- **THEN** `load_config()` returns a `ButlerConfig` with all parsed fields
- **AND** environment variable references like `${VAR_NAME}` are resolved from `os.environ`

#### Scenario: Missing required field blocks startup
- **WHEN** `butler.toml` is missing `butler.name` or `butler.port`
- **THEN** `load_config()` raises `ConfigError` with a descriptive message

#### Scenario: Unresolved env var blocks startup
- **WHEN** a config string contains `${VAR_NAME}` and `VAR_NAME` is not set in the environment
- **THEN** `load_config()` raises `ConfigError` listing all missing variable names

#### Scenario: Schema-based DB isolation requires schema field
- **WHEN** `butler.db.name` is set to `"butlers"` (consolidated DB) but `butler.db.schema` is absent
- **THEN** `load_config()` raises `ConfigError` requiring the schema field

#### Scenario: Runtime type validation at config load time
- **WHEN** `[runtime].type` is set to an unknown adapter name
- **THEN** `load_config()` raises `ConfigError` via `get_adapter()` failing with `ValueError`

### Requirement: Startup Phase Sequence
The daemon implements a phased startup with 17 steps (as documented in the daemon module docstring). Steps are executed sequentially and a failure at any step triggers partial cleanup of already-initialized modules.

#### Scenario: Full startup succeeds
- **WHEN** all configuration, credentials, database, modules, and runtime are valid
- **THEN** the daemon completes all 17 startup phases in order
- **AND** the MCP SSE server is listening on the configured port

#### Scenario: Startup step ordering
- **WHEN** the daemon starts
- **THEN** the phases execute in order: config load, telemetry init, module loading (topo sort), module config validation, env credential validation, DB provisioning, core migrations, module migrations, credential store creation + module credential validation (non-fatal) + core credential validation (fatal), module on_startup hooks, spawner creation with runtime adapter, schedule sync, MCP tool registration (core then modules then approval gates), SSE server start, switchboard heartbeat, scheduler loop, liveness reporter

#### Scenario: Startup failure triggers module cleanup
- **WHEN** a startup phase fails after some modules have been initialized
- **THEN** all already-initialized modules receive `on_shutdown()` calls in reverse topological order

### Requirement: Runtime Adapter Binary Verification
At startup the daemon verifies the configured runtime adapter's CLI binary is available on PATH via `shutil.which()`.

#### Scenario: Missing runtime binary
- **WHEN** the configured runtime type's binary (e.g., `claude` for `claude-code`) is not found on PATH
- **THEN** the daemon logs a warning but continues startup (binary check is advisory)

### Requirement: Graceful Shutdown Contract
The daemon implements a multi-phase shutdown sequence that is bounded by a configurable timeout (default 30s from `butler.shutdown.timeout_s`).

#### Scenario: Ordered shutdown
- **WHEN** shutdown is triggered
- **THEN** the daemon: (a) stops the MCP server, (b) stops accepting new triggers, (c) drains in-flight sessions within timeout, (d) cancels switchboard heartbeat, (e) closes switchboard MCP client, (f) cancels scheduler loop (waits for in-progress tick to finish), (g) cancels liveness reporter, (h) shuts down modules in reverse topological order, (i) closes DB pool

#### Scenario: In-flight session drain with timeout
- **WHEN** in-flight sessions exist during shutdown
- **THEN** the spawner waits up to `shutdown_timeout_s` for them to complete
- **AND** remaining sessions are cancelled after the timeout expires

### Requirement: Health and Status Tool
Every butler exposes a `status` core MCP tool that returns identity, health, loaded modules, and uptime information.

#### Scenario: Status tool returns butler identity
- **WHEN** the `status` tool is called
- **THEN** it returns the butler name, description, loaded module names, uptime, and health state

### Requirement: Core Tool Surface
Every butler daemon registers a fixed set of core MCP tools as defined in `CORE_TOOL_NAMES`.

#### Scenario: Core tools are registered
- **WHEN** the daemon completes startup
- **THEN** the following tools are available: `status`, `trigger`, `route.execute`, `tick`, `state_get`, `state_set`, `state_delete`, `state_list`, `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`, `sessions_list`, `sessions_get`, `sessions_summary`, `sessions_daily`, `top_sessions`, `schedule_costs`, `notify`, `remind`, `get_attachment`, `module.states`, `module.set_enabled`

### Requirement: Internal Scheduler Loop
The daemon runs an internal asyncio loop that calls `tick()` at a configurable interval (`butler.scheduler.tick_interval_seconds`, default 60s).

#### Scenario: Periodic tick dispatch
- **WHEN** the daemon is running
- **THEN** it calls `tick()` every `tick_interval_seconds` to evaluate and dispatch due scheduled tasks

### Requirement: Liveness Reporter
Non-switchboard butlers periodically POST to the Switchboard heartbeat endpoint at a configurable interval (`butler.scheduler.heartbeat_interval_seconds`, default 120s).

#### Scenario: Heartbeat to switchboard
- **WHEN** the butler is not the switchboard and has a configured `switchboard_url`
- **THEN** it sends periodic HTTP POST heartbeats to the Switchboard's `/api/switchboard/heartbeat` endpoint

### Requirement: Module Startup Fault Isolation
Each module's startup is tracked independently. A module that fails at any phase (credentials, config, migration, startup, tools) is marked with its failure status (`failed` or `cascade_failed`) but does not block other modules from starting.

#### Scenario: Module startup failure is isolated
- **WHEN** a module fails during its startup phase
- **THEN** the module is marked with status `failed` and its failure phase and error are recorded
- **AND** modules that depend on the failed module are marked `cascade_failed`
- **AND** non-dependent modules continue their startup normally
