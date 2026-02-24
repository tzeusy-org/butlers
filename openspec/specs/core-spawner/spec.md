# LLM CLI Spawner

## Purpose
Manages ephemeral AI runtime invocations for a butler, including locked-down MCP config generation, multi-runtime adapter support, semaphore-based concurrency control, session lifecycle logging, credential isolation, memory context injection, and trace-correlated telemetry.

## ADDED Requirements

### Requirement: Multi-Runtime Adapter Support
The spawner delegates to a `RuntimeAdapter` abstract base class. Three concrete adapters are registered: `claude-code` (ClaudeCodeAdapter via Claude Agent SDK), `codex` (CodexAdapter via subprocess), and `gemini` (GeminiAdapter via subprocess). Each adapter implements `invoke()`, `build_config_file()`, `parse_system_prompt_file()`, `binary_name`, `create_worker()`, and `reset()`.

#### Scenario: Claude Code adapter invocation
- **WHEN** the butler's runtime type is `claude-code`
- **THEN** the ClaudeCodeAdapter builds `McpSSEServerConfig`/`McpHttpServerConfig` objects and calls `claude_agent_sdk.query()` asynchronously
- **AND** parses `ResultMessage` and `ToolUseBlock` from the response stream

#### Scenario: Codex adapter invocation
- **WHEN** the butler's runtime type is `codex`
- **THEN** the CodexAdapter runs `codex exec --json --full-auto` as an async subprocess
- **AND** embeds the system prompt into the initial prompt payload (Codex has no system prompt flag)
- **AND** parses JSON-lines output for result text, tool calls, and usage metrics

#### Scenario: Gemini adapter invocation
- **WHEN** the butler's runtime type is `gemini`
- **THEN** the GeminiAdapter runs the `gemini` binary with `--system-prompt` and `--prompt` flags
- **AND** filters env vars to exclude `ANTHROPIC_API_KEY` and include `GOOGLE_API_KEY`

#### Scenario: Unknown runtime type fails at config load
- **WHEN** `get_adapter(type_str)` is called with an unregistered runtime type
- **THEN** a `ValueError` is raised listing available adapters

### Requirement: Ephemeral MCP Config Generation
Each invocation generates a locked-down MCP configuration pointing exclusively at this butler's MCP server URL. The runtime session ID is appended as a query parameter to the MCP URL for tool-call-to-session correlation.

#### Scenario: MCP config includes only butler's server
- **WHEN** the spawner prepares an invocation
- **THEN** the `mcp_servers` dict contains exactly one entry keyed by the butler's name
- **AND** the entry's URL points to `http://localhost:<port>/sse` (or `/mcp`) with the runtime session ID as a query parameter

### Requirement: Concurrency Control
The spawner uses an `asyncio.Semaphore` with a configurable concurrency limit (`max_concurrent_sessions`, default 1). When all slots are occupied, new triggers queue up to `max_queued_sessions` (default 100) before being rejected.

#### Scenario: Serial dispatch (default)
- **WHEN** `max_concurrent_sessions=1` and a trigger arrives while another session is in-flight
- **THEN** the new trigger waits for the semaphore slot

#### Scenario: Self-trigger deadlock prevention
- **WHEN** `trigger_source="trigger"` and all concurrency slots are occupied (`_value == 0`)
- **THEN** the invocation is rejected immediately with `success=False` to prevent deadlock

#### Scenario: Queue full backpressure
- **WHEN** all concurrency slots are occupied and the waiter queue reaches `max_queued_sessions`
- **THEN** the invocation is rejected immediately with a queue-full error

### Requirement: Session Lifecycle Serialization
Each invocation creates a session record before the runtime call and completes it after, regardless of success or failure. Sessions are trace-correlated via OpenTelemetry span context.

#### Scenario: Successful session
- **WHEN** a runtime invocation completes successfully
- **THEN** `session_create()` is called before invocation and `session_complete()` is called after with `success=True`, output text, tool calls, duration, and token counts

#### Scenario: Failed session
- **WHEN** a runtime invocation raises an exception
- **THEN** `session_complete()` is called with `success=False`, the error message, and duration
- **AND** the runtime adapter's `reset()` method is called for cleanup

### Requirement: Trigger Source Tracking
Valid trigger sources are: `tick`, `external`, `trigger`, `route`, and `schedule:<task-name>`. The trigger source is passed through to session creation for audit.

#### Scenario: Schedule trigger source
- **WHEN** a task named `daily_digest` fires via the scheduler
- **THEN** the session's `trigger_source` is `"schedule:daily_digest"`

### Requirement: Credential Isolation
The spawner builds an explicit environment dict for the runtime process containing only: `PATH` (for shebang resolution), core API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`), butler-level required/optional env vars, and module credential vars. Credentials are resolved DB-first via `CredentialStore.resolve()` with env-var fallback. Undeclared env vars do not leak through.

#### Scenario: Only declared credentials are passed
- **WHEN** the spawner builds the runtime environment
- **THEN** only `PATH`, core API keys, declared butler env vars, and declared module credential vars are included
- **AND** other host environment variables are excluded

### Requirement: Memory Context Injection
When the memory module is enabled, the spawner fetches memory context via `fetch_memory_context()` before invocation and appends it to the system prompt. On successful completion, it stores the session output as an episode via `store_session_episode()`. Both operations are fail-open (log and continue).

#### Scenario: Memory context injected into system prompt
- **WHEN** the memory module is enabled and context is available
- **THEN** the memory context is appended to the base system prompt separated by a blank line

#### Scenario: Memory failure does not block invocation
- **WHEN** memory context retrieval fails
- **THEN** the failure is logged and the invocation proceeds with the base system prompt only

### Requirement: System Prompt Composition
The system prompt is read from `CLAUDE.md` in the butler's config directory. Include directives (`<!-- @include path/to/file.md -->`) are resolved relative to the roster directory. Shared prompt snippets (`BUTLER_SKILLS.md`, `MCP_LOGGING.md`) are appended if present.

#### Scenario: System prompt with includes
- **WHEN** `CLAUDE.md` contains `<!-- @include shared/NOTIFY.md -->`
- **THEN** the directive is replaced with the contents of `roster/shared/NOTIFY.md`

### Requirement: Drain for Shutdown
The spawner supports `stop_accepting()` to reject new triggers and `drain(timeout)` to wait for in-flight sessions to complete, cancelling remaining sessions after timeout.

#### Scenario: Drain completes within timeout
- **WHEN** `drain(timeout=30.0)` is called and all sessions finish within 30 seconds
- **THEN** the method returns successfully

#### Scenario: Drain timeout cancels sessions
- **WHEN** `drain(timeout=30.0)` is called and sessions are still running after 30 seconds
- **THEN** remaining in-flight tasks are cancelled
