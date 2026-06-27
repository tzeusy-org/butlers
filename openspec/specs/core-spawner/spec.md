# LLM CLI Spawner

## Purpose
Manages ephemeral AI runtime invocations for a butler, including locked-down MCP config generation, multi-runtime adapter support, semaphore-based concurrency control, session lifecycle logging, credential isolation, memory context injection, and trace-correlated telemetry.

## Requirements

### Requirement: Multi-Runtime Adapter Support
The spawner SHALL delegate to a `RuntimeAdapter` abstract base class. Four concrete adapters are registered: `claude` (ClaudeCodeAdapter via subprocess), `codex` (CodexAdapter via subprocess), `gemini` (GeminiAdapter via subprocess), and `opencode` (OpenCodeAdapter via subprocess). Each adapter SHALL implement `invoke()`, `build_config_file()`, `parse_system_prompt_file()`, `binary_name`, `create_worker()`, and `reset()`.

The spawner SHALL maintain a lazy adapter pool (`dict[str, RuntimeAdapter]`) keyed by runtime type. When model resolution selects a runtime type different from the TOML-configured adapter, the spawner instantiates the required adapter on demand via `get_adapter(type).create_worker()` and caches it for reuse.

#### Scenario: Claude Code adapter invocation via subprocess
- **WHEN** the butler's runtime type is `claude`
- **THEN** the ClaudeCodeAdapter locates the `claude` binary on PATH via `shutil.which("claude")`
- **AND** invokes it as an async subprocess with flags: `-p`, `--output-format stream-json`, `--bare`, `--no-session-persistence`, `--permission-mode bypassPermissions`, `--strict-mcp-config`
- **AND** passes the system prompt via `--system-prompt <prompt>`
- **AND** passes the MCP config file via `--mcp-config <path>` (the file written by `build_config_file()`)
- **AND** passes the model via `--model <model>` when specified
- **AND** passes additional CLI arguments from `runtime_args` after the fixed flags
- **AND** passes the user prompt as the final positional argument
- **AND** parses `stream-json` JSON-line events from stdout for result text, tool calls, and token usage
- **AND** captures stderr to a per-butler log file at `{log_root}/butlers/{butler_name}_cc_stderr.log`
- **AND** populates `last_process_info` with subprocess metadata (pid, exit_code, command, stderr)

#### Scenario: Claude Code binary not found
- **WHEN** the butler's runtime type is `claude`
- **AND** the `claude` binary is not found on PATH
- **THEN** `invoke()` SHALL raise `FileNotFoundError` with an actionable message including install instructions

#### Scenario: Claude Code process timeout
- **WHEN** the `claude` subprocess exceeds the configured timeout
- **THEN** the adapter SHALL kill the subprocess and raise `TimeoutError`
- **AND** `last_process_info` SHALL contain pid, exit_code of -1, and stderr noting the timeout

#### Scenario: Claude Code process non-zero exit
- **WHEN** the `claude` subprocess exits with a non-zero exit code
- **THEN** the adapter SHALL raise `RuntimeError` with the exit code and stderr content
- **AND** `last_process_info` SHALL contain the full subprocess metadata

#### Scenario: Claude Code MCP config isolation
- **WHEN** the adapter builds the invocation command
- **THEN** the `--strict-mcp-config` flag SHALL be included
- **AND** only MCP servers declared in the butler's config SHALL be available to the Claude session
- **AND** host machine Claude Code MCP settings SHALL NOT leak into the session

#### Scenario: Claude Code token usage extraction
- **WHEN** the `stream-json` output contains a `result` event with a `usage` object
- **THEN** the adapter SHALL extract `input_tokens`, `output_tokens`, `cache_read_input_tokens`, and `cache_creation_input_tokens` from the usage object
- **AND** return them in the usage dict of the `invoke()` return tuple

#### Scenario: Claude Code tool call extraction
- **WHEN** the `stream-json` output contains assistant message events with `tool_use` content blocks
- **THEN** the adapter SHALL extract each tool call's `id`, `name`, and `input` fields
- **AND** return them as normalized dicts in the tool_calls list of the `invoke()` return tuple

#### Scenario: Claude Code environment isolation
- **WHEN** the adapter spawns the `claude` subprocess
- **THEN** only the explicitly provided `env` dict SHALL be passed as the subprocess environment
- **AND** no host environment variables SHALL leak through

#### Scenario: Claude Code credential injection via CLI Runtime Authentication
- **WHEN** the butler's runtime type is `claude`
- **AND** the user has configured an Anthropic API key via the dashboard Settings → CLI Runtime Authentication card
- **THEN** the credential store SHALL contain the key under `cli-auth/claude` with `env_var=ANTHROPIC_API_KEY`
- **AND** the spawner's credential isolation logic SHALL resolve `ANTHROPIC_API_KEY` from the credential store
- **AND** inject it into the subprocess environment dict
- **AND** the `claude` CLI binary SHALL use the injected key for API authentication

#### Scenario: Claude CLI auth provider registered in registry
- **WHEN** the CLI auth registry is loaded
- **THEN** a provider with `name="claude"`, `auth_mode="api_key"`, `env_var="ANTHROPIC_API_KEY"`, and `runtime="claude"` SHALL be registered
- **AND** the dashboard Settings page SHALL render a Claude row in the CLI Runtime Authentication card
- **AND** the row SHALL support API key entry, storage, and health probing

#### Scenario: Claude Code max_turns parameter
- **WHEN** `invoke()` is called with a `max_turns` parameter
- **THEN** the parameter SHALL be accepted without error
- **AND** the parameter SHALL NOT be enforced by the CLI (no equivalent flag exists)
- **AND** timeout remains the primary safety mechanism

#### Scenario: Claude Code worker creation
- **WHEN** `create_worker()` is called on a ClaudeCodeAdapter instance
- **THEN** a new independent ClaudeCodeAdapter instance SHALL be returned
- **AND** the new instance SHALL share the same `butler_name` and `log_root` configuration

#### Scenario: Claude Code config file generation
- **WHEN** `build_config_file()` is called with MCP server configurations
- **THEN** the adapter SHALL write a JSON file at `{tmp_dir}/mcp.json` with the structure `{"mcpServers": {...}}`
- **AND** the file path SHALL be compatible with the `--mcp-config` CLI flag

#### Scenario: Claude Code system prompt file reading
- **WHEN** `parse_system_prompt_file()` is called with a butler config directory
- **THEN** the adapter SHALL read `CLAUDE.md` from that directory
- **AND** return the file contents as a string, or empty string if the file is missing

#### Scenario: Process log written after Claude Code invocation
- **WHEN** the spawner completes a Claude Code adapter invocation (success or failure)
- **AND** `runtime.last_process_info` returns a non-null dict
- **THEN** the spawner SHALL write the process info to `session_process_logs`

#### Scenario: Codex adapter invocation
- **WHEN** the butler's runtime type is `codex`
- **THEN** the CodexAdapter runs `codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check` as an async subprocess
- **AND** writes a per-invocation TOML config file at `<tmp>/.codex/config.toml` with MCP server entries (url, transport)
- **AND** sets `HOME` to the temp directory so the CLI discovers `~/.codex/config.toml` during its earliest init phase
- **AND** embeds the system prompt into the initial prompt payload (Codex has no system prompt flag)
- **AND** parses JSON-lines output for result text, tool calls, and usage metrics
- **AND** cleans up the temp directory in a `finally` block
- **AND** warns when MCP servers were configured but only `command_execution` events (shell commands) were recorded — indicating MCP connection failure

#### Scenario: Gemini adapter invocation
- **WHEN** the butler's runtime type is `gemini`
- **THEN** the GeminiAdapter runs the `gemini` binary with `--system-prompt` and `--prompt` flags
- **AND** passes declared butler env vars to the subprocess

#### Scenario: OpenCode adapter invocation
- **WHEN** the butler's runtime type is `opencode`
- **THEN** the OpenCodeAdapter runs `opencode run --format json` as an async subprocess
- **AND** writes a temporary JSONC config with MCP servers, instructions, and permissions
- **AND** parses JSON output for result text, tool calls, and usage metrics

#### Scenario: Unknown runtime type fails at config load
- **WHEN** `get_adapter(type_str)` is called with an unregistered runtime type
- **THEN** a `ValueError` is raised listing available adapters

#### Scenario: Lazy adapter pool instantiation
- **WHEN** model resolution returns a `runtime_type` not yet in the adapter pool
- **THEN** the spawner calls `get_adapter(runtime_type)` to get the adapter class, instantiates it, calls `create_worker()`, and caches the result
- **AND** subsequent invocations with the same `runtime_type` reuse the cached adapter

#### Scenario: Adapter pool does not pre-instantiate
- **WHEN** the spawner starts
- **THEN** only the TOML-configured adapter is instantiated eagerly
- **AND** other adapters are instantiated lazily on first use

### Requirement: Ephemeral MCP Config Generation
Each invocation SHALL generate a locked-down MCP configuration pointing exclusively at this butler's MCP server URL. The runtime session ID SHALL be appended as a query parameter to the MCP URL for tool-call-to-session correlation.

#### Scenario: MCP config includes only butler's server
- **WHEN** the spawner prepares an invocation
- **THEN** the `mcp_servers` dict contains exactly one entry keyed by the butler's name
- **AND** the entry's URL points to `http://localhost:<port>/sse` (or `/mcp`) with the runtime session ID as a query parameter

### Requirement: Concurrency Control
The spawner SHALL use an `asyncio.Semaphore` with a configurable concurrency limit (`max_concurrent_sessions`, default 1). When all slots are occupied, new triggers SHALL queue up to `max_queued_sessions` (default 100) before being rejected.

#### Scenario: Serial dispatch (default)
- **WHEN** `max_concurrent_sessions=1` and a trigger arrives while another session is in-flight
- **THEN** the new trigger waits for the semaphore slot

#### Scenario: Self-trigger deadlock prevention
- **WHEN** `trigger_source="trigger"` and all concurrency slots are occupied (`_value == 0`)
- **THEN** the invocation is rejected immediately with `success=False` to prevent deadlock

#### Scenario: Queue full backpressure
- **WHEN** all concurrency slots are occupied and the waiter queue reaches `max_queued_sessions`
- **THEN** the invocation is rejected immediately with a queue-full error

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
Valid trigger sources are: `tick`, `external`, `trigger`, `route`, `healing`, and `schedule:<task-name>`. The trigger source SHALL be passed through to session creation for audit.

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

### Requirement: Credential Isolation
The spawner SHALL build an explicit environment dict for the runtime process containing only: `PATH` (for shebang resolution), declared `[butler.env]` vars, module credential vars, and CLI auth provider credentials (e.g. `ANTHROPIC_API_KEY` for the Claude runtime). Runtime authentication uses either CLI-level OAuth tokens (device-code flow) or API keys entered via the dashboard Settings → CLI Runtime Authentication card, depending on the provider's `auth_mode`. Credentials SHALL be resolved DB-first via `CredentialStore.resolve()` with env-var fallback. Undeclared env vars SHALL NOT leak through.

#### Scenario: Only declared credentials are passed
- **WHEN** the spawner builds the runtime environment
- **THEN** only `PATH`, declared `[butler.env]` vars, and declared module credential vars are included
- **AND** other host environment variables are excluded

### Requirement: Memory Context Injection
When the memory module is enabled, the spawner SHALL fetch memory context via `fetch_memory_context()` before invocation and append it to the system prompt. On successful completion, it SHALL store the session output as an episode via `store_session_episode()`. Both operations SHALL be fail-open (log and continue).

#### Scenario: Memory context injected into system prompt
- **WHEN** the memory module is enabled and context is available
- **THEN** the memory context is appended to the base system prompt separated by a blank line

#### Scenario: Memory failure does not block invocation
- **WHEN** memory context retrieval fails
- **THEN** the failure is logged and the invocation proceeds with the base system prompt only

### Requirement: System Prompt Composition
The system prompt is read from `CLAUDE.md` in the butler's config directory. Include directives (`<!-- @include path/to/file.md -->`) are resolved relative to the roster directory. Shared prompt snippets (`BUTLER_SKILLS.md`, `MCP_LOGGING.md`) are appended if present. When active situational context signals exist, the spawner SHALL call `get_active_context()` and `format_context_preamble()` to prepend a context summary to the system prompt. The context preamble SHALL appear after the identity preamble and before the memory context block. Context preamble injection is fail-open: if the context query fails, the spawner logs the error and proceeds without context.

#### Scenario: System prompt with includes
- **WHEN** `CLAUDE.md` contains `<!-- @include shared/NOTIFY.md -->`
- **THEN** the directive is replaced with the contents of `roster/shared/NOTIFY.md`

#### Scenario: Context preamble injected when signals active
- **WHEN** the spawner prepares an invocation and `get_active_context()` returns active signals
- **THEN** the context preamble is prepended to the system prompt after the identity preamble
- **AND** the preamble format follows `format_context_preamble()` output

#### Scenario: No context preamble when no signals
- **WHEN** the spawner prepares an invocation and `get_active_context()` returns an empty list
- **THEN** no context preamble is added to the system prompt

#### Scenario: Context query failure does not block invocation
- **WHEN** the `get_active_context()` call raises an exception
- **THEN** the failure is logged at WARNING level
- **AND** the invocation proceeds with the system prompt without context preamble

### Requirement: Dynamic Model Resolution at Spawn Time
The spawner SHALL resolve the model dynamically at spawn time using the model catalog instead of reading a static model from `butler.toml`. The `trigger()` method gains a `complexity` parameter that drives model selection. The spawner MAY use same-tier failover only after the initial catalog candidate has been selected.

#### Scenario: Trigger with complexity parameter
- **WHEN** `trigger(prompt, trigger_source, complexity="high")` is called
- **THEN** the spawner calls `resolve_model(butler_name, "high")` to determine the runtime type, model ID, and extra args

#### Scenario: Trigger without complexity parameter
- **WHEN** `trigger(prompt, trigger_source)` is called without a complexity parameter
- **THEN** the complexity defaults to `medium`

#### Scenario: Catalog resolution overrides static fallback model
- **WHEN** `resolve_model()` returns a result
- **THEN** the returned `runtime_type`, `model_id`, and `extra_args` are used for the invocation
- **AND** the module-private `_FALLBACK_MODEL_ID` constant in `butlers.core.spawner` is ignored

#### Scenario: Catalog empty fallback to static defaults
- **WHEN** `resolve_model()` returns `None` (no matching entries) or fails
- **THEN** the spawner falls back to the module-private `_FALLBACK_MODEL_ID` constant paired with `DEFAULT_RUNTIME_TYPE` from `butlers.core.runtimes`; these are hard-coded last-resort constants, not butler-scoped config

#### Scenario: Runtime args sourced only from the catalog
- **WHEN** catalog resolution returns `extra_args`
- **THEN** the catalog `extra_args` are forwarded verbatim to the adapter as `runtime_args`
- **AND** there is no butler-scoped args fallback; when the catalog returns no args, the kwarg is omitted

#### Scenario: Session record includes model resolution metadata
- **WHEN** a session is created via `session_create()`
- **THEN** the session record includes: the resolved `model` (model_id from catalog or the static fallback constant), `runtime_type`, `complexity` tier, and resolution source (`catalog` or `static_fallback`)

#### Scenario: Initial catalog candidate establishes failover tier
- **WHEN** `resolve_model()` returns a catalog result for a trigger
- **THEN** the spawner SHALL treat that result's effective complexity tier as the
  failover tier for the logical session
- **AND** subsequent automatic failover attempts SHALL use only that exact tier

#### Scenario: Catalog resolution failure uses static fallback
- **WHEN** initial catalog resolution returns `None` for every eligible tier or raises
  before a catalog candidate is selected
- **THEN** the spawner SHALL use the existing static fallback behavior
- **AND** same-tier model failover SHALL NOT run because no catalog tier was established

### Requirement: Runtime Failure Classification
The spawner SHALL classify runtime failures before deciding whether automatic model
failover is safe.

#### Scenario: Systemic runtime failure is eligible
- **WHEN** a runtime adapter fails before any side-effect-capable work is observed
- **AND** the failure is classified as systemic infrastructure or provider failure
- **THEN** the spawner MAY attempt same-tier model failover if another eligible
  candidate exists

#### Scenario: Captured tool calls make failure ineligible
- **WHEN** captured tool calls for the failed attempt are non-empty
- **THEN** the spawner SHALL classify the failure as not failover-eligible
- **AND** it SHALL NOT start a second model attempt for the same logical session

#### Scenario: Classifier defaults closed
- **WHEN** the classifier receives an unknown exception type, ambiguous adapter error,
  or incomplete process metadata
- **THEN** it SHALL classify the failure as not failover-eligible

### Requirement: Logical Session Attempt Orchestration
The spawner SHALL keep automatic model failover attempts bounded and auditable.

#### Scenario: Successful fallback completes logical session once
- **WHEN** the primary model fails with a failover-eligible error
- **AND** a fallback model succeeds
- **THEN** exactly one logical session completion SHALL be recorded
- **AND** the session's final model SHALL be the successful fallback model
- **AND** provenance SHALL record the failed primary attempt

#### Scenario: Non-eligible failure completes without retry
- **WHEN** a runtime invocation fails with a non-failover-eligible error
- **THEN** the spawner SHALL preserve existing failure behavior
- **AND** it SHALL record no fallback invocation

#### Scenario: Attempt cap prevents infinite retry
- **WHEN** same-tier failover is active
- **THEN** the number of attempts SHALL be bounded by the number of eligible same-tier
  catalog candidates
- **AND** no catalog entry SHALL be invoked more than once for the same logical session

### Requirement: Drain for Shutdown
The spawner SHALL support `stop_accepting()` to reject new triggers and `drain(timeout)` to wait for in-flight sessions to complete, cancelling remaining sessions after timeout.

#### Scenario: Drain completes within timeout
- **WHEN** `drain(timeout=30.0)` is called and all sessions finish within 30 seconds
- **THEN** the method returns successfully

#### Scenario: Drain timeout cancels sessions
- **WHEN** `drain(timeout=30.0)` is called and sessions are still running after 30 seconds
- **THEN** remaining in-flight tasks are cancelled

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

### Requirement: Spawner resolves hot config fields per-spawn from the model catalog
The Spawner SHALL resolve the hot fields (model, runtime_type, args, session_timeout_s) on every `trigger()` call rather than reading them from the static `ButlerConfig`. As of migration `core_073` these fields live on `public.model_catalog` (resolved per complexity tier), not on the `runtime_config` table. The Spawner calls `resolve_model_with_effective_tier()` (`src/butlers/core/model_routing.py`) to obtain the catalog entry id, runtime_type, args, and session_timeout_s for the chosen tier. The `RuntimeConfigAccessor` is still consulted, but only for cold fields (core_groups, max_concurrent, max_queued).

Note: an earlier design sourced these hot fields from `RuntimeConfigAccessor.get()`. That path was superseded by the catalog (core_073). The scenarios below reflect the catalog-based reality.

Source: RFC 0001 §Trigger Pipeline, RFC 0002 §Core Tools, migration core_073
Scope: v1-mandatory

#### Scenario: Model resolved from the catalog with a constant fallback
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL resolve the model from `public.model_catalog` via `resolve_model_with_effective_tier()`
- **AND** if catalog resolution fails or returns no result, the Spawner SHALL fall back to the module-level `_FALLBACK_MODEL_ID` constant (`src/butlers/core/spawner.py`), not the toml config

#### Scenario: Runtime type from the catalog
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL use the catalog entry's `runtime_type` to select the runtime adapter

#### Scenario: Args from the catalog
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL merge the catalog entry's `args` with any per-trigger args

#### Scenario: Session timeout from the catalog
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL use the catalog entry's `session_timeout_s` for the `asyncio.wait_for` timeout
- **AND** the Spawner SHALL forward that same timeout value into `runtime.invoke(...)`

#### Scenario: Session timeout is per invocation only
- **WHEN** `trigger()` is called by a higher-level workflow orchestrator
- **THEN** `session_timeout_s` limits only that spawned runtime session
- **AND** any broader workflow deadline is enforced by the caller, not by the Spawner

#### Scenario: Dashboard model change takes effect on the next spawn
- **WHEN** a user changes the model for a complexity tier via the Models tab (`PATCH /api/model-settings`, backed by `public.model_catalog`)
- **THEN** new sessions spawned afterward SHALL use the updated catalog entry, since the model is resolved from the catalog per `trigger()`

#### Scenario: Accessor DB failure during trigger — use stale cache
- **WHEN** `accessor.get()` is called during `trigger()` but the DB query fails
- **AND** the accessor has a previously cached value
- **THEN** the Spawner SHALL proceed with the stale cached config
- **AND** log a warning about the stale config

### Requirement: Cold fields read at construction only
The Spawner SHALL read `max_concurrent` and `max_queued` from the accessor once at construction time. These values are used to size the asyncio.Semaphore and queue limit.

Source: RFC 0001 §Concurrency Control
Scope: v1-mandatory

#### Scenario: Concurrency limit from DB
- **WHEN** the Spawner is constructed
- **THEN** the asyncio.Semaphore capacity SHALL be set from `accessor.get().max_concurrent`

#### Scenario: Queue limit from DB
- **WHEN** the Spawner is constructed
- **THEN** the max queued sessions limit SHALL be set from `accessor.get().max_queued`

#### Scenario: Concurrency change requires restart
- **WHEN** a user changes `max_concurrent` via the dashboard
- **THEN** the change SHALL NOT take effect until the daemon is restarted

### Requirement: Ingestion Event Propagation Through Trigger Pipeline
`Spawner.trigger()` SHALL accept an optional `ingestion_event_id` parameter and pass it unchanged through `_run()` to `session_create()`. Callers that originate from a switchboard ingest (routing handlers in particular) SHALL provide this parameter so the resulting session row joins back to the `public.ingestion_events.id` that produced it. Internally-triggered sessions (tick, scheduler, manual trigger) MAY omit it.

#### Scenario: Route handler propagates the ingestion event UUID
- **WHEN** `route_inbox_processing` invokes `_spawner.trigger(...)` for a routed message
- **THEN** the call SHALL pass `ingestion_event_id=<route_request_id>` (which is the same UUID7 the switchboard ingest writes into `public.ingestion_events.id`)
- **AND** the resulting `{schema}.sessions` row SHALL have `ingestion_event_id = <route_request_id>`

#### Scenario: Tick / schedule sessions omit ingestion event id
- **WHEN** a scheduler-fired or tick-fired session calls `Spawner.trigger(...)`
- **THEN** the call SHALL leave `ingestion_event_id` at its default (`None`)
- **AND** the resulting session row SHALL have `ingestion_event_id = NULL`

#### Scenario: Spawner trigger signature stable across runtimes
- **WHEN** any runtime adapter (`opencode`, `codex`, `claude_code`) is invoked through the spawner
- **THEN** the `ingestion_event_id` value SHALL remain a property of the session row only and SHALL NOT be exposed to the runtime process via env, prompt prefix, or MCP context
- **AND** runtime adapters SHALL NOT accept this parameter — the propagation chain ends at `session_create`

### Requirement: Degenerate Session Guardrails
The spawner SHALL evaluate a session for degenerate behavior after the runtime invocation returns and the tool-call records have been merged, and SHALL terminate the session (by raising `RuntimeError`) when any guardrail is exceeded. Detection is runtime-agnostic: the checks consume the same merged tool-call list and token usage the spawner already reads for session logging, so every registered adapter (`claude`, `codex`, `gemini`, `opencode`) is covered uniformly.

Guardrails are evaluated as **post-session checks**, not in-flight cancellation. The runtime subprocess has already exited by the time the checks run; the guardrail decides whether the completed session counts as a success or a typed failure. The checks run in a fixed order and the first to trip wins: degenerate loop → tool-call budget → token budget.

Three independent budgets SHALL be enforced, each OR-combined with the others:

1. **Consecutive-identical-call count** (`_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD`, default `6`): The maximum run of back-to-back tool calls sharing one `(name, input_fingerprint)` signature. Only *adjacent* duplicates count; any non-identical call resets the streak. The signature uses the call's `input_fingerprint` when present, otherwise a canonical fingerprint of the call's `input`/`args`/`arguments`/`parameters` payload.
2. **Cumulative tool-call count** (`max_tool_calls`, default `_DEFAULT_MAX_TOOL_CALLS = 0`): Total tool calls observed in the merged session list. A value of `0` disables the check (the shipped default leaves it off).
3. **Cumulative input tokens** (`max_token_budget`, default `None`): Sum of `input_tokens` reported by the adapter for the session. `None` disables the check.

These thresholds are spawner-level parameters / module constants, not `RuntimeConfigAccessor` HOT fields and not `RuntimeSeedConfig` columns. The defaults above are the shipped values; callers MAY pass overrides through `trigger()` / the internal invoke path.

#### Scenario: Consecutive identical tool calls trip the loop detector
- **WHEN** a completed session's merged tool-call list contains `_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD` or more consecutive calls sharing one `(name, input_fingerprint)` signature
- **THEN** the spawner SHALL raise `RuntimeError` whose message begins with `degenerate_tool_loop:` and names the looping tool
- **AND** the session SHALL be recorded with `success=False` and the guardrail message in the session `error` column
- **AND** the tool calls that ran SHALL be preserved on the failed session record

#### Scenario: Non-identical intervening call resets the streak
- **WHEN** a session issues identical calls `A, A, A`, then a different call `B`, then `A` again
- **THEN** the consecutive-identical streak SHALL reset at `B`
- **AND** the session SHALL NOT be flagged as a degenerate loop unless a single uninterrupted run of identical calls reaches the threshold

#### Scenario: Tool-call budget exceeded
- **WHEN** the cumulative tool-call count for a session exceeds a configured non-zero `max_tool_calls`
- **THEN** the spawner SHALL raise `RuntimeError` whose message begins with `tool_call_budget_exceeded:`
- **AND** the session SHALL be recorded with `success=False`

#### Scenario: Tool-call budget disabled by default
- **WHEN** `max_tool_calls` is `0` (the shipped default `_DEFAULT_MAX_TOOL_CALLS`)
- **THEN** the tool-call budget check SHALL be skipped regardless of how many tool calls the session made

#### Scenario: Input-token budget exceeded
- **WHEN** the session's reported cumulative `input_tokens` exceeds a configured `max_token_budget`
- **THEN** the spawner SHALL raise `RuntimeError` whose message begins with `token_budget_exceeded:`
- **AND** the session SHALL be recorded with `success=False`

#### Scenario: Token budget disabled when unset or usage unknown
- **WHEN** `max_token_budget` is `None`, or the adapter reported no `input_tokens` for the session
- **THEN** the token-budget check SHALL be skipped

#### Scenario: Guardrail termination suppresses same-tier failover
- **WHEN** a guardrail raises `RuntimeError` from the post-invocation success path
- **THEN** the failover classifier SHALL recognise the guardrail marker substring in the exception message (`degenerate_tool_loop`, `tool_call_budget_exceeded`, `token_budget_exceeded`)
- **AND** SHALL classify the failure as not failover-eligible so no automatic retry is attempted
- **AND** the suppressed-failover metric SHALL be recorded for observability
