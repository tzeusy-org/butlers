# LLM CLI Spawner — Self-Healing Fallback

## MODIFIED Requirements

### Requirement: Spawner Session Lifecycle
Each invocation creates a session record before the runtime call and completes it after, regardless of success or failure. Sessions are trace-correlated via OpenTelemetry span context. After completing a runtime invocation, the spawner SHALL check `runtime.last_process_info` and, if non-null and a session_id and database pool are available, write the process metadata to the `session_process_logs` table via `session_process_log_write()`. This applies to both the success path (after `session_complete` with `success=True`) and the error path (after `session_complete` with `success=False`). The write is best-effort: exceptions are caught and logged at DEBUG level without affecting the session result or propagating to the caller. On the error path, after all existing error handling (session_complete, process log, runtime reset, audit entry), the spawner SHALL invoke the self-healing dispatcher as a **fallback** — this catches hard crashes where the butler agent never got a chance to call the `report_error` MCP tool.

#### Scenario: Successful session
- **WHEN** a runtime invocation completes successfully
- **THEN** `session_create()` is called before invocation and `session_complete()` is called after with `success=True`, output text, tool calls, duration, and token counts

#### Scenario: Failed session — spawner fallback dispatch
- **WHEN** a runtime invocation raises an exception
- **THEN** `session_complete()` is called with `success=False`, the error message, and duration
- **AND** the runtime adapter's `reset()` method is called for cleanup
- **AND** the self-healing dispatcher is invoked via `asyncio.create_task()` as a **fallback** with the raw exception, traceback, session_id, butler config, and trigger_source

#### Scenario: Fallback is secondary to module path
- **WHEN** a butler agent called `report_error` during its session for the same error before the session crashed
- **AND** the spawner fallback also fires for the same exception
- **THEN** the novelty gate deduplicates — the second dispatch (fallback) sees the active attempt from the first (module) and appends the session ID instead of creating a duplicate

#### Scenario: Dispatcher receives exception and traceback
- **WHEN** the spawner invokes the fallback dispatcher from the except block
- **THEN** it captures `sys.exc_info()` BEFORE any cleanup code runs
- **AND** passes the live traceback to `dispatch_healing()` for fingerprinting

#### Scenario: Process log written after successful runtime invocation
- **WHEN** the spawner completes a runtime invocation successfully
- **AND** `runtime.last_process_info` returns a non-null dict
- **THEN** the spawner writes the process info to `session_process_logs` after calling `session_complete`

#### Scenario: Process log written after failed runtime invocation
- **WHEN** the spawner catches an exception from `runtime.invoke()`
- **AND** `runtime.last_process_info` returns a non-null dict
- **THEN** the spawner writes the process info to `session_process_logs` after calling `session_complete`

#### Scenario: No process log write for SDK-based runtime
- **WHEN** the spawner completes a ClaudeCodeAdapter invocation
- **AND** `runtime.last_process_info` returns None
- **THEN** no process log write is attempted

#### Scenario: Process log write failure is non-fatal
- **WHEN** the `session_process_log_write()` call raises any exception
- **THEN** the exception is logged at DEBUG level and the spawner continues normally

#### Scenario: Healing dispatcher failure is non-fatal
- **WHEN** the fallback dispatcher task raises an exception
- **THEN** the exception is logged at WARNING level
- **AND** the original `SpawnerResult` is unaffected (already returned)

#### Scenario: Finally block exceptions do not trigger healing
- **WHEN** an exception occurs in the spawner's `finally` block (metrics, span cleanup, context clearing)
- **THEN** no healing dispatch occurs for that exception

### Requirement: Trigger Source Tracking
Valid trigger sources are: `tick`, `external`, `trigger`, `route`, `healing`, and `schedule:<task-name>`. The trigger source is passed through to session creation for audit.

#### Scenario: Schedule trigger source
- **WHEN** a task named `daily_digest` fires via the scheduler
- **THEN** the session's `trigger_source` is `"schedule:daily_digest"`

#### Scenario: Healing trigger source
- **WHEN** the self-healing module or fallback dispatcher spawns an investigation agent
- **THEN** the session's `trigger_source` is `"healing"`

#### Scenario: Healing sessions skip fallback dispatcher
- **WHEN** a session with `trigger_source = "healing"` fails
- **THEN** the spawner fallback dispatcher is NOT invoked (no recursive healing)
- **AND** the spawner's except block checks `trigger_source` BEFORE creating the dispatch task

### Requirement: Healing Session Semaphore Bypass
When the spawner's `trigger()` is called with `trigger_source = "healing"`, the per-butler session semaphore SHALL be bypassed. The global semaphore SHALL still be acquired. This is essential for the module path where the calling session is still holding the per-butler semaphore.

#### Scenario: Healing bypasses per-butler semaphore
- **WHEN** `trigger(prompt, trigger_source="healing")` is called and the per-butler semaphore has 0 available slots
- **THEN** the healing session proceeds without acquiring the per-butler semaphore

#### Scenario: Healing acquires global semaphore
- **WHEN** `trigger(prompt, trigger_source="healing")` is called
- **THEN** the global semaphore is still acquired

### Requirement: Healing Session MCP Restriction
When spawning a healing agent session (`trigger_source = "healing"`), the spawner SHALL generate an empty MCP config. The healing agent has access to the codebase via the worktree and shell tools only.

#### Scenario: Healing session has no MCP servers
- **WHEN** the spawner builds the MCP config for a `trigger_source = "healing"` session
- **THEN** the `mcp_servers` dict is empty

#### Scenario: Healing session receives GitHub token
- **WHEN** the spawner builds the environment for a healing session
- **THEN** the env includes `GH_TOKEN` resolved from the credential store
- **AND** includes `PATH` for tool discovery
- **AND** no other butler-specific credentials or env vars are passed

## ADDED Requirements

### Requirement: Healing Configuration in butler.toml
The spawner SHALL support healing-related configuration that the self-healing module and fallback dispatcher both read.

#### Scenario: Default healing config
- **WHEN** `butler.toml` has no `[modules.self_healing]` section
- **THEN** the self-healing module is not loaded and the spawner fallback is also disabled

#### Scenario: Healing config fields
- **WHEN** `[modules.self_healing]` is present
- **THEN** the following fields are recognized:
  - `enabled` (bool, default: `true`) — module loaded
  - `severity_threshold` (int, default: `2`)
  - `max_concurrent` (int, default: `2`)
  - `cooldown_minutes` (int, default: `60`)
  - `circuit_breaker_threshold` (int, default: `5`)
  - `timeout_minutes` (int, default: `30`)

#### Scenario: Spawner fallback uses module config
- **WHEN** the spawner fallback fires for a hard crash
- **THEN** it reads the self-healing module's config for gate thresholds
- **AND** if the module is not loaded, the fallback is also disabled (no separate `[healing]` section needed)
