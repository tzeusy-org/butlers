## MODIFIED Requirements

### Requirement: Multi-Runtime Adapter Support
Runtime launches SHALL delegate to a `RuntimeAdapter` abstract base class, both for the goal invocations that the goal runner launches on the session host and for the engineering executors that the Spawner launches. Four concrete adapters are registered: `claude` (ClaudeCodeAdapter via subprocess), `codex` (CodexAdapter via subprocess), `gemini` (GeminiAdapter via subprocess), and `opencode` (OpenCodeAdapter via subprocess). Each adapter SHALL implement `invoke()`, `build_config_file()`, `parse_system_prompt_file()`, `binary_name`, `create_worker()`, and `reset()`.

For a goal invocation, an adapter SHALL disable every runtime built-in tool (shell, filesystem, web fetch, local MCP servers) and every provider-hosted tool, SHALL NOT pass a permission- or sandbox-bypass flag, and SHALL use a strict MCP config that names only the broker. A runtime and model tuple whose adapter cannot prove these restrictions SHALL be ineligible for goal invocations. Engineering executors keep their existing sandboxed contracts.

The launcher (the goal runner or the Spawner) SHALL maintain a lazy adapter pool (`dict[str, RuntimeAdapter]`) keyed by runtime type. When model resolution selects a runtime type different from the TOML-configured adapter, the launcher instantiates the required adapter on demand via `get_adapter(type).create_worker()` and caches it for reuse.

Source: RFC 0038 D3, D10, D16 and D18
Scope: v1-mandatory

#### Scenario: Claude Code adapter invocation via subprocess
- **WHEN** the selected runtime type is `claude`
- **THEN** the ClaudeCodeAdapter locates the `claude` binary on PATH via `shutil.which("claude")` and invokes it as an async subprocess with the flags `-p`, `--output-format stream-json`, `--bare`, `--no-session-persistence` and `--strict-mcp-config`, adding `--permission-mode bypassPermissions` only for an engineering executor and never for a goal invocation
- **AND** it passes the system prompt via `--system-prompt <prompt>`, the MCP config file via `--mcp-config <path>` (the file written by `build_config_file()`), the model via `--model <model>` when specified, additional CLI arguments from `runtime_args` after the fixed flags, and the user prompt as the final positional argument
- **AND** it parses `stream-json` JSON-line events from stdout for result text, tool calls, and token usage
- **AND** it captures stderr to a per-invocation log for a goal invocation and, for an engineering executor, to the per-butler log file at `{log_root}/butlers/{butler_name}_cc_stderr.log`
- **AND** populates `last_process_info` with subprocess metadata (pid, exit_code, command, stderr)

#### Scenario: Goal invocation disables runtime built-in tools
- **WHEN** an adapter launches a goal invocation
- **THEN** it applies the runtime's deny-all tool settings and passes no `--permission-mode bypassPermissions`, `--dangerously-bypass-approvals-and-sandbox` or `--sandbox=false` flag
- **AND** the session has no shell, filesystem, web or local MCP tool and no provider-hosted tool such as server-side web search, remote MCP connectors or code execution

#### Scenario: Claude Code binary not found
- **WHEN** the butler's runtime type is `claude`
- **AND** the `claude` binary is not found on PATH
- **THEN** `invoke()` SHALL raise `FileNotFoundError` with an actionable message including install instructions

#### Scenario: Claude Code process timeout
- **WHEN** the `claude` subprocess exceeds the configured timeout
- **THEN** the adapter SHALL kill the subprocess and its whole descendant process domain and raise `TimeoutError`
- **AND** `last_process_info` SHALL contain pid, exit_code of -1, and stderr noting the timeout

#### Scenario: Claude Code process non-zero exit
- **WHEN** the `claude` subprocess exits with a non-zero exit code
- **THEN** the adapter SHALL raise `RuntimeError` with the exit code and stderr content
- **AND** `last_process_info` SHALL contain the full subprocess metadata

#### Scenario: Claude Code MCP config isolation
- **WHEN** the adapter builds the invocation command
- **THEN** the `--strict-mcp-config` flag SHALL be included
- **AND** a goal invocation's config SHALL name only the broker's goal endpoint, and an engineering executor's config SHALL name no MCP server
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
- **WHEN** the selected runtime type is `claude`
- **AND** the user has configured an Anthropic API key via the dashboard Settings → CLI Runtime Authentication card
- **THEN** the credential store SHALL contain the key under `cli-auth/claude` with `env_var=ANTHROPIC_API_KEY`
- **AND** a goal invocation SHALL receive it only as the invocation's staged model credential on the session host, never through credential-store access from the invocation
- **AND** for an engineering-executor launch, the Spawner's credential isolation logic SHALL resolve `ANTHROPIC_API_KEY` from the credential store and inject it into the subprocess environment dict
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
- **AND** for a goal invocation the goal runner enforces the invocation's turn, token and spend limits from the runtime's event stream between tool calls; otherwise timeout remains the primary safety mechanism

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
- **WHEN** the selected runtime type is `codex`
- **THEN** the CodexAdapter runs `codex exec --json --skip-git-repo-check` as an async subprocess, adding `--dangerously-bypass-approvals-and-sandbox` only for an engineering executor and never for a goal invocation
- **AND** writes a per-invocation TOML config file at `<tmp>/.codex/config.toml` with MCP server entries (url, transport) and, for a goal invocation, the runtime's deny-all tool settings
- **AND** sets `HOME` to the temp directory so the CLI discovers `~/.codex/config.toml` during its earliest init phase, and embeds the system prompt into the initial prompt payload (Codex has no system prompt flag)
- **AND** parses JSON-lines output for result text, tool calls, and usage metrics, and cleans up the temp directory in a `finally` block
- **AND** warns when MCP servers were configured but only `command_execution` events (shell commands) were recorded — indicating MCP connection failure

#### Scenario: Gemini adapter invocation
- **WHEN** the selected runtime type is `gemini`
- **THEN** the GeminiAdapter runs the `gemini` binary with `--system-prompt` and `--prompt` flags
- **AND** passes only the environment its launcher provides; for a goal invocation that is `PATH`, the staged model credential and the MCP config

#### Scenario: OpenCode adapter invocation
- **WHEN** the selected runtime type is `opencode`
- **THEN** the OpenCodeAdapter runs `opencode run --format json` as an async subprocess
- **AND** writes a temporary JSONC config with MCP servers, instructions, and permissions, whose permissions deny every built-in tool for a goal invocation
- **AND** parses JSON output for result text, tool calls, and usage metrics

#### Scenario: Unknown runtime type fails at config load
- **WHEN** `get_adapter(type_str)` is called with an unregistered runtime type
- **THEN** a `ValueError` is raised listing available adapters

#### Scenario: Lazy adapter pool instantiation
- **WHEN** model resolution returns a `runtime_type` not yet in the adapter pool
- **THEN** the launcher calls `get_adapter(runtime_type)` to get the adapter class, instantiates it, calls `create_worker()`, and caches the result
- **AND** subsequent invocations with the same `runtime_type` reuse the cached adapter

#### Scenario: Adapter pool does not pre-instantiate
- **WHEN** the spawner starts
- **THEN** only the TOML-configured adapter is instantiated eagerly
- **AND** other adapters are instantiated lazily on first use

### Requirement: Concurrency Control
The spawner SHALL use an `asyncio.Semaphore` with a configurable concurrency limit (`max_concurrent_sessions`, default 1). When all slots are occupied, new launches SHALL queue up to `max_queued_sessions` (default 100) before being rejected. The Spawner SHALL launch only engineering executors; the concurrency of goal invocations SHALL be bounded per goal by its budget, not by this semaphore.

Source: RFC 0038 D7, D10 and D18; RFC 0001 §Concurrency Control
Scope: v1-mandatory

#### Scenario: Serial dispatch (default)
- **WHEN** `max_concurrent_sessions=1` and a trigger arrives while another session is in-flight
- **THEN** the new trigger waits for the semaphore slot

#### Scenario: Self-trigger deadlock prevention
- **WHEN** a running session would need another Spawner slot to start a further session
- **THEN** no path exists for it to do so, because the `trigger` tool and `trigger_source="trigger"` no longer exist
- **AND** a goal invocation can obtain further work only through `goal.delegate`, which the goal runner serves outside this semaphore

#### Scenario: Queue full backpressure
- **WHEN** all concurrency slots are occupied and the waiter queue reaches `max_queued_sessions`
- **THEN** the invocation is rejected immediately with a queue-full error

### Requirement: Spawner Session Lifecycle
Each Spawner invocation creates a session record before the runtime call and completes it after, regardless of success or failure. The Spawner SHALL create session records only for the engineering executors it launches; goal invocations SHALL be recorded in the goal ledger. Sessions are trace-correlated via OpenTelemetry span context. After completing a runtime invocation, the spawner SHALL check `runtime.last_process_info` and, if non-null and a session_id and database pool are available, write the process metadata to the `session_process_logs` table via `session_process_log_write()`. This applies to both the success path (after `session_complete` with `success=True`) and the error path (after `session_complete` with `success=False`). The write is best-effort: exceptions are caught and logged at DEBUG level without affecting the session result or propagating to the caller. The error path SHALL preserve ordinary failure evidence and cleanup without dispatching a per-butler investigation; QA owns independent session/log discovery under RFC 0015.

ID: REQ-core-spawner-005
Source: RFC 0038 D17 and D18; RFC 0015 centralized QA discovery/dispatch; bu-1fe7xv rounds 3-4; core-spawner existing session evidence contract
Scope: v1-mandatory

#### Scenario: Successful session
- **WHEN** a runtime invocation completes successfully
- **THEN** `session_create()` is called before invocation and `session_complete()` is called after with `success=True`, output text, tool calls, duration, and token counts

#### Scenario: Failed session — spawner fallback dispatch
- **WHEN** a runtime invocation raises an exception
- **THEN** `session_complete()` is called with `success=False`, the original error message, and duration
- **AND** the runtime adapter's `reset()` method is called for cleanup
- **AND** no per-butler investigation task is created by the Spawner

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

#### Scenario: Fallback is secondary to module path
- **WHEN** a registered relay reports an agent-observed error and a runtime invocation independently fails
- **THEN** the report is received through the centralized QA boundary and ordinary Spawner failure evidence remains available
- **AND** no additional per-butler fallback investigation is dispatched

#### Scenario: Dispatcher receives exception and traceback
- **WHEN** a runtime raises an exception with traceback evidence
- **THEN** the Spawner preserves its ordinary failure, process and captured-tool evidence without passing an exception/traceback to a local investigation dispatcher
- **AND** agent-reported structured exceptions retain fingerprint/traceback handling at the centralized report_error relay boundary

#### Scenario: Healing dispatcher failure is non-fatal
- **WHEN** QA reception is unavailable while a runtime invocation fails
- **THEN** the original session error, reset and finally cleanup remain authoritative
- **AND** no per-butler fallback dispatcher is invoked or allowed to mask that error

#### Scenario: Finally block exceptions do not trigger healing
- **WHEN** an exception occurs in the spawner's `finally` block (metrics, span cleanup, context clearing)
- **THEN** no healing dispatch occurs for that exception

### Requirement: Trigger Source Tracking
The Spawner's valid trigger sources SHALL be the engineering-executor sources `qa` and `healing`, and the trigger source SHALL be passed through to session creation for audit. The sources `tick`, `external`, `trigger`, `route` and `schedule:<task-name>` SHALL NOT start a Spawner session: the work they named is admitted as a goal, whose origin, cause and invocation lineage (`goal_id`, `invocation_id`, `depth`, `epoch`) take the place of a trigger source.

ID: REQ-core-spawner-006
Source: RFC 0038 D5, D17 and D18; core-spawner trigger audit; RFC 0015 centralized QA; bu-lsxqb0.6 and bu-1fe7xv rounds 3-4
Scope: v1-mandatory

#### Scenario: Schedule trigger source
- **WHEN** a prompt-mode task named `daily_digest` becomes due
- **THEN** no Spawner session with `trigger_source="schedule:daily_digest"` is created
- **AND** the obligation's goal records origin `obligation` and the occurrence of `daily_digest` it was admitted for

#### Scenario: QA investigation trigger source
- **WHEN** QA dispatches an investigation agent
- **THEN** the session's `trigger_source` is `"qa"`

#### Scenario: Healing trigger source
- **WHEN** an existing shared legacy healing dispatcher explicitly spawns an investigation session
- **THEN** the session's `trigger_source` is `"healing"`
- **AND** this does not enable direct investigation dispatch by the relay module or Spawner crash handler

#### Scenario: Healing sessions skip fallback dispatcher
- **WHEN** a session with `trigger_source = "healing"` fails
- **THEN** its failure remains recorded without invoking a per-butler fallback dispatcher
- **AND** the centralized QA self-recursion barrier remains authoritative for QA-origin findings

### Requirement: Credential Isolation
The Spawner SHALL build an explicit environment dict for each engineering executor containing only `PATH` and the credentials that executor's contract declares (for example `GH_TOKEN`, and the CLI auth provider credential its runtime needs, such as `ANTHROPIC_API_KEY` for the Claude runtime). Runtime authentication uses either CLI-level OAuth tokens (device-code flow) or API keys entered via the dashboard Settings → CLI Runtime Authentication card, depending on the provider's `auth_mode`. Spawner credentials SHALL be resolved DB-first via `CredentialStore.resolve()` with env-var fallback. A goal invocation's environment SHALL contain only `PATH`, its staged model credential and its MCP config, and SHALL NOT contain `[butler.env]` vars, module or connector credentials, database credentials, or credential-store access. Undeclared env vars SHALL NOT leak through.

Source: RFC 0038 D3 and D18
Scope: v1-mandatory

The following current-runtime contract applies only before the goal/executor cutover; it does not extend the engineering executor or goal invocation credential authority defined above. In particular, its explicit `[butler.env]` behavior SHALL NOT apply to either future invocation class.

For existing pre-cutover butler runtime processes only, the spawner SHALL build an explicit environment dict for the runtime process containing only: `PATH` (for shebang resolution), declared `[butler.env]` vars, distributed trace context, and CLI auth provider credentials (e.g. `ANTHROPIC_API_KEY` for the Claude runtime). Runtime authentication uses either CLI-level OAuth tokens (device-code flow) or API keys entered via the dashboard Settings → CLI Runtime Authentication card, depending on the provider's `auth_mode`. Credentials SHALL be resolved DB-first via `CredentialStore.resolve()` with env-var fallback. Undeclared env vars SHALL NOT leak through.

ID: REQ-core-spawner-010
Source: Credential Isolation; RFC 0038 live security fix bu-13itjw
Scope: v1-mandatory

#### Scenario: Only declared credentials are passed
- **WHEN** the spawner builds the runtime environment for an existing pre-cutover butler
- **THEN** only `PATH`, declared `[butler.env]` vars, and distributed trace context are included
- **AND** other host environment variables are excluded

#### Scenario: Module credentials are not passed
- **WHEN** module credentials are available in the host environment or daemon credential store for an existing pre-cutover butler runtime
- **THEN** the spawner SHALL NOT automatically resolve or forward them into runtime children
- **AND** an explicit `[butler.env]` declaration retains its existing operator-authorized behavior, subject to owner-auth and database-admin withholding
- **AND** module startup validation and daemon-side tool credential resolution remain intact

#### Scenario: Engineering executor declared credentials
- **WHEN** the Spawner builds an engineering executor's runtime environment
- **THEN** only `PATH` and the executor's declared credential vars are included
- **AND** other host environment variables are excluded

#### Scenario: Goal invocations hold no domain or connector credential
- **WHEN** the goal runner launches an invocation for a goal that needs a Messenger send capability
- **THEN** the invocation's environment holds no Telegram bot token, email password or other connector credential
- **AND** the invocation can reach Messenger's capability only through the broker

### Requirement: System Prompt Composition
For an engineering executor the Spawner launches, the system prompt is read from `CLAUDE.md` in the launching butler's config directory. Include directives (`<!-- @include path/to/file.md -->`) are resolved relative to the roster directory. Shared prompt snippets (`BUTLER_SKILLS.md`, `MCP_LOGGING.md`) are appended if present. The Spawner SHALL NOT inject situational context into an engineering executor's prompt (RFC 0038 D18): it no longer calls `get_active_context()` or `format_context_preamble()` at launch, and an executor sees only the anonymized payloads RFC 0015 allows.

A goal invocation's system prompt SHALL NOT be read from any butler's `CLAUDE.md`. The goal runner SHALL compose a lead's system prompt from the versioned lead policy (`roster/switchboard/goal-policy/LEAD.md`), the owner profile, the goal input with third-party spans fenced as data, an index of domains and their guidance, and, on recovery or escalation, the summary of the goal record, effect ledger and returned child results; it SHALL compose a child's from its brief. Situational context, memory and domain guidance SHALL reach a goal invocation only through `context.search`, domain read capabilities and `guidance.load`, which return stamped results.

Source: RFC 0038 D2, D6, D9, D14, D15 and D18; RFC 0009
Scope: v1-mandatory

#### Scenario: System prompt with includes
- **WHEN** `CLAUDE.md` contains `<!-- @include shared/NOTIFY.md -->`
- **THEN** the directive is replaced with the contents of `roster/shared/NOTIFY.md`

#### Scenario: Context preamble injected when signals active
- **WHEN** the Spawner prepares an engineering executor while situational context signals are active
- **THEN** no context preamble is prepended to the executor's system prompt
- **AND** the executor's prompt carries only the anonymized payload RFC 0015 allows

#### Scenario: No context preamble when no signals
- **WHEN** the Spawner prepares an engineering executor and no situational context signal is active
- **THEN** no context preamble is added to the system prompt

#### Scenario: Context query failure does not block invocation
- **WHEN** the situational-context store is unavailable while the Spawner prepares an engineering executor
- **THEN** the launch is unaffected, because the Spawner makes no context query

#### Scenario: Lead prompt comes from the lead policy
- **WHEN** the goal runner launches a lead for an owner goal
- **THEN** its system prompt contains the lead policy, the owner profile, the fenced goal input and the domain index
- **AND** no domain `CLAUDE.md`, memory context or situational-context preamble is injected

#### Scenario: Replacement lead starts from the goal record
- **WHEN** recovery or escalation launches a replacement lead
- **THEN** its system prompt also carries the goal record, the goal's exposure, the effect-ledger summary and the returned child results

### Requirement: Blind-Spot Preamble Injection
The goal runner SHALL include, in each domain's entry of a lead's domain index, a layer summarizing the health of each module-declared "expected signal" of that domain — a signal a module has registered in `public.expected_signals` as a name it expects to arrive on a cadence. Declared signal-key patterns are resolved per domain from a static per-module registry (`MODULE_BLIND_SPOT_SIGNAL_PATTERNS` in `blind_spot_declarations.py`), keyed by the domain's enabled module names.

Evaluation SHALL be fail-closed by construction: `evaluate_declared_signals()` re-evaluates each declared signal fresh and catches any exception, setting `query_failed=True` rather than raising or silently omitting results.

Behavior:
- silent (no blind-spot layer in the domain's index entry) when there are no declared signal patterns for the domain's enabled modules, or when every declared signal currently evaluates PRESENT and the query itself succeeded.
- byte-identical lead prompt to one with no declared blind-spot signals when every declared signal is PRESENT.
- typed block naming each non-PRESENT signal's `signal_key`, `producer`, `last_observed_at`, and the evaluator's `now` clock, when at least one declared signal is not PRESENT.
- explicit "source health could not be evaluated" text (`BLIND_SPOT_QUERY_FAILED_TEXT`) instead of any signal detail, when the query itself raised.

The layer SHALL be gated by a per-domain kill switch: `runtime_config.blind_spot_preamble_enabled` (added by migration `core_228`, default `true`). Reading the flag SHALL be fail-open — if the runtime_config accessor raises, or the column does not yet exist on an unmigrated schema, the layer defaults to enabled.

`GET /api/butlers/{name}` SHALL project the identical evaluation (`declared_signal_patterns()` + `evaluate_declared_signals()`) so the dashboard's `blind_spots` field never disagrees with what the next lead's domain index will say for that domain.

Source: RFC 0038 D15; RFC 0029
Scope: v1-mandatory

#### Scenario: No declared signals means byte-identical prompt
- **WHEN** a domain's enabled modules declare no blind-spot signal patterns
- **THEN** that domain's index entry carries no blind-spot layer
- **AND** the lead prompt is unchanged from one composed with the blind-spot feature absent

#### Scenario: All declared signals present means byte-identical prompt
- **WHEN** every signal_key_like_pattern-matched row in `public.expected_signals` evaluates to `measurability="present"` for a domain
- **THEN** `fetch_blind_spot_preamble()` returns `None` for that domain
- **AND** the lead prompt is unchanged from one composed without the layer

#### Scenario: Non-present signal renders a typed block
- **WHEN** at least one declared signal of a domain evaluates to `absent` or `unmeasurable`
- **THEN** that domain's index entry includes a block naming that signal's key, producer, last_observed_at (or "never observed"), and the evaluator's clock

#### Scenario: Query failure renders the fail-closed disclosure
- **WHEN** `evaluate_declared_signals()` catches an exception while querying or re-evaluating a domain's expected signals
- **THEN** that domain's blind-spot layer reads "source health could not be evaluated" instead of omitting the layer or fabricating signal state

#### Scenario: Kill switch disables the layer
- **WHEN** `runtime_config.blind_spot_preamble_enabled` is `false` for a domain
- **THEN** the goal runner SHALL NOT call `fetch_blind_spot_preamble()` for that domain and no blind-spot layer is added to its index entry, regardless of declared signal state

#### Scenario: Dashboard and prompt agree
- **WHEN** `GET /api/butlers/{name}` is called for a domain with a non-present declared signal
- **THEN** the response's `blind_spots` list contains that signal
- **AND** the next lead's domain index carries the same signal in that domain's blind-spot layer

## REMOVED Requirements

### Requirement: Ephemeral MCP Config Generation
**Reason**: RFC 0038 D3 and D7 replace the per-butler config that bound a session to its own butler's endpoint with a runtime session identifier in the query string; no session SHALL be configured against a domain endpoint, and a query-string identifier SHALL carry no authority.

**Migration**: See "Goal Invocation MCP Config Names Only the Broker" (REQ-core-spawner-009) in this spec, `REQ-capability-authorization-001` (sandbox) and `REQ-capability-authorization-008` (invocation tokens).

### Requirement: Memory Context Injection
**Reason**: RFC 0038 D2 and D15 remove the per-butler sessions this requirement served: a lead's starting context is fixed by D15, and memory SHALL reach a goal only through `context.search` and domain read capabilities that return stamped results, with memory writes going through the owning domain's capabilities under the goal's assertion.

**Migration**: Memory retrieval is `context.search` (RFC 0038 D8) under `REQ-capability-authorization-006`; memory writes follow `REQ-capability-authorization-006` and `REQ-capability-authorization-007`; memory consolidation, which today runs as a Spawner session with `trigger_source="schedule:consolidation"`, becomes an obligation goal (`REQ-goal-orchestration-008`).

### Requirement: Ingestion Event Propagation Through Trigger Pipeline
**Reason**: RFC 0038 D1 and D7 delete route dispatch and `route.execute`, so no Spawner session SHALL originate from a switchboard ingest and `Spawner.trigger()` has no ingestion event to carry.

**Migration**: The ingestion event identifier is carried by the goal admitted from the ingest envelope; see "Goals Carry Ingestion Event Identifier" (`REQ-core-sessions-001`).

### Requirement: Interactive Reply Delivery Accounting
**Reason**: RFC 0038 D1 and D7 remove route-triggered sessions, the only sessions this accounting inspected; no Spawner session SHALL carry `trigger_source="route"`.

**Migration**: Each reply is a `communicate` effect recorded in the effect ledger with its receipt (`REQ-capability-authorization-009`), and the lead reports an undelivered reply as an unresolved outcome rather than success (`REQ-goal-orchestration-006`).

## ADDED Requirements

### Requirement: Goal Invocation MCP Config Names Only the Broker
Each goal invocation SHALL receive a locked-down MCP configuration naming exactly one server: the broker's goal endpoint on the control plane's session listener, authenticated by the invocation's bearer token. The configuration SHALL NOT name any domain butler's endpoint and SHALL NOT carry a session identifier, trigger source or any other authority in its URL query string; tool-call-to-invocation correlation SHALL come from the token the broker resolves. Each domain's canonical MCP listing SHALL stay complete for its infrastructure clients, and the broker's listing SHALL follow RFC 0027 discovery over the invocation's grant. An engineering executor launched by the Spawner SHALL receive an empty MCP configuration.

ID: REQ-core-spawner-009
Source: RFC 0038 D3, D7 and D18; RFC 0027 Invariant 8
Scope: v1-mandatory

#### Scenario: Config names only the broker
- **WHEN** the goal runner prepares a lead or child invocation
- **THEN** the `mcp_servers` dict contains exactly one entry, pointing at the broker's goal endpoint on the session listener with the invocation token as a bearer credential
- **AND** the broker's `tools/list` for that token returns only the grant's capabilities under namespaced names (`<domain>__<tool>`) plus the goal tools

#### Scenario: Query-string identifiers carry no authority
- **WHEN** a request reaches the broker with a session or invocation identifier in its URL query string but no valid bearer token
- **THEN** the broker refuses the call
- **AND** a domain that receives a model-reachable call without a valid assertion refuses it regardless of any query-string identifier

#### Scenario: Infrastructure clients keep the complete canonical list
- **WHEN** Switchboard, a connector, the scheduler or another infrastructure client connects to a domain's canonical endpoint
- **THEN** it receives the domain's complete registered MCP list under its existing endpoint contract

#### Scenario: Engineering executors get no MCP servers
- **WHEN** the Spawner prepares a `qa` or `healing` session
- **THEN** the `mcp_servers` dict is empty
