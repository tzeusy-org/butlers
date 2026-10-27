## MODIFIED Requirements

### Requirement: Multi-Runtime Adapter Support
Runtime launches SHALL delegate to a `RuntimeAdapter` abstract base class, both for the goal invocations that the goal runner launches on the session host and for the engineering executors that the Spawner launches. Four concrete adapters are registered: `claude` (ClaudeCodeAdapter via subprocess), `codex` (CodexAdapter via subprocess), `gemini` (GeminiAdapter via subprocess), and `opencode` (OpenCodeAdapter via subprocess). Each adapter SHALL implement `invoke()`, `build_config_file()`, `parse_system_prompt_file()`, `binary_name`, `create_worker()`, and `reset()`.

For a goal invocation, an adapter SHALL disable every runtime built-in tool (shell, filesystem, web fetch, local MCP servers) and every provider-hosted tool, SHALL NOT pass a permission- or sandbox-bypass flag, and SHALL use a strict MCP config that names only the broker. A runtime and model tuple whose adapter cannot prove these restrictions SHALL be ineligible for goal invocations. Engineering executors keep their existing sandboxed contracts.

The launcher (the goal runner or the Spawner) SHALL maintain a lazy adapter pool (`dict[str, RuntimeAdapter]`) keyed by runtime type. When model resolution selects a runtime type different from the TOML-configured adapter, the launcher instantiates the required adapter on demand via `get_adapter(type).create_worker()` and caches it for reuse.

Source: RFC 0038 D3, D10, D16 and D18
Scope: v1-mandatory

ID: REQ-core-spawner-016

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
- **AND** passes only the environment its launcher provides plus its adapter-owned file pointers; for a goal invocation that is `PATH`, the staged model credential, the MCP config and the `GEMINI_CLI_SYSTEM_SETTINGS_PATH` pointer, which names a settings file staged inside the invocation's sandbox

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

Until the RFC 0038 goal-runtime cutover, the existing ordinary dispatch paths retain the following source-audit contract; it does not authorize a goal invocation or re-enable the retired local healing dispatcher:

Valid trigger sources include `tick`, `external`, `trigger`, `route`, `qa`, and `schedule:<task-name>`. Historical `healing` provenance SHALL remain readable and its existing isolation/recursion safeguards SHALL remain intact; it SHALL NOT enable the retired local dispatcher. The trigger source SHALL be passed through to session creation for audit.

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
- **WHEN** an existing session carries historical `trigger_source="healing"` provenance
- **THEN** its original trigger source remains unchanged
- **AND** neither the core QA relay nor the Spawner crash handler starts a local investigation
- **AND** new centralized QA investigations retain `trigger_source="qa"`

#### Scenario: Healing sessions skip fallback dispatcher
- **WHEN** a session with `trigger_source = "healing"` fails
- **THEN** its failure remains recorded without invoking a per-butler fallback dispatcher
- **AND** the centralized QA self-recursion barrier remains authoritative for QA-origin findings

### Requirement: Credential Isolation
The Spawner SHALL build an explicit environment dict for each engineering executor containing only `PATH` and the credentials that executor's contract declares (for example `GH_TOKEN`, and the CLI auth provider credential its runtime needs, such as `ANTHROPIC_API_KEY` for the Claude runtime). Runtime authentication uses either CLI-level OAuth tokens (device-code flow) or API keys entered via the dashboard Settings → CLI Runtime Authentication card, depending on the provider's `auth_mode`. Spawner credentials SHALL be resolved DB-first via `CredentialStore.resolve()` with env-var fallback. A goal invocation's environment SHALL contain only `PATH`, its staged model credential, its MCP config and adapter-owned file pointers, and SHALL NOT contain `[butler.env]` vars, module or connector credentials, database credentials, or credential-store access. An adapter-owned file pointer is a variable the runtime adapter sets whose value is only the path of a file or directory staged inside that invocation's own sandbox; its value SHALL NOT be secret, and the files it names SHALL hold no secret other than the staged model credential and the invocation's own MCP config. Undeclared env vars SHALL NOT leak through.

Until the RFC 0038 goal-runtime cutover, ordinary legacy sessions retain the following isolation contract; it grants no additional credentials to goal invocations or engineering executors:

The spawner SHALL build an explicit environment dict containing only `PATH` (for shebang resolution), declared `[butler.env]` required/optional variables, distributed-tracing variables, and the selected runtime's authentication material. Runtime authentication uses CLI-level OAuth tokens (device-code flow) or the selected provider's configured API key, depending on its `auth_mode`. Declared variables SHALL be resolved through `CredentialStore.resolve()` when a store is present, without implicit environment fallback; the no-store path MAY resolve them from the process environment. Owner-auth and administrative database variables SHALL always be withheld, even if declared.

Module channel credentials SHALL NOT be added merely because a module declares or resolves them. Module credential validation and daemon-side resolution SHALL remain available to the modules. An operator MAY explicitly declare a channel variable in `[butler.env]`; that declaration remains subject to the owner-auth and administrative-variable exclusions. Undeclared host variables SHALL NOT leak through, and an explicit empty environment SHALL NOT become ambient environment inheritance at the runtime adapter.

Source: RFC 0038 D3 and D18; bu-13itjw legacy credential isolation
Scope: v1-mandatory

ID: REQ-core-spawner-010

#### Scenario: Only declared credentials are passed
- **GIVEN** a legacy session before the RFC 0038 goal-runtime cutover
- **WHEN** the spawner builds a normal runtime environment
- **THEN** only `PATH`, declared `[butler.env]` variables, tracing, and selected runtime authentication are included
- **AND** undeclared module channel credentials and other host environment variables are excluded
- **AND** daemon-side module validation and credential resolution remain unchanged

#### Scenario: Explicitly declared channel variable
- **GIVEN** a legacy session before the RFC 0038 goal-runtime cutover
- **WHEN** an operator declares a module channel variable in `[butler.env]`
- **THEN** the variable is resolved by the normal declared-variable path
- **AND** owner-auth and administrative database variables remain withheld

#### Scenario: Empty runtime environment
- **WHEN** the spawner supplies an empty environment mapping
- **THEN** the runtime adapter preserves that explicit mapping rather than inheriting the daemon environment
- **AND** invocation-local MCP configuration variables MAY be added without inheriting unrelated host variables

#### Scenario: Engineering executor declared credentials
- **WHEN** the Spawner builds an engineering executor's runtime environment
- **THEN** only `PATH` and the executor's declared credential vars are included
- **AND** other host environment variables are excluded

#### Scenario: Goal invocations hold no domain or connector credential
- **WHEN** the goal runner launches an invocation for a goal that needs a Messenger send capability
- **THEN** the invocation's environment holds no Telegram bot token, email password or other connector credential
- **AND** the invocation can reach Messenger's capability only through the broker

#### Scenario: Goal invocations admit only non-secret pointers into their own sandbox
- **WHEN** a runtime adapter adds a variable to a goal invocation's environment beyond `PATH`, the staged model credential and the MCP config
- **THEN** the variable is admitted only if its value is the path of a file or directory staged inside that invocation's own sandbox and the named files hold no secret other than the staged model credential and the invocation's own MCP config
- **AND** a variable that points outside the sandbox, or whose value or named file carries any other secret, makes that adapter ineligible for goal invocations

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

## ADDED Requirements

### Requirement: Starting envelopes have one bounded composition path
The runner SHALL consume S5's exact starting-envelope/query schema, finite runtime-config snapshots, stable prefix and whole-item limits through brokered authorized reads. It SHALL NOT compose domain daemon prompts, grant from profile metadata or inline untrusted text under a typed-shape exception.

ID: REQ-core-spawner-014
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S5; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: Dynamic evidence changes
- **WHEN** a new situation, recall item or open loop is added to a repeated owner invocation
- **THEN** immutable policy/profile/contract prefix bytes stay before the breakpoint, with dynamic labelled evidence afterward

#### Scenario: A domain exceeds the budget
- **WHEN** a read times out or would exceed the whole packet/item limit
- **THEN** the domain is explicitly degraded or complete items are omitted with reasons; no body is detached from its label/reference

#### Scenario: A child starts with a narrower brief
- **WHEN** the runner launches a depth-one child
- **THEN** only its brief and authorized relevant envelope are supplied, with inherited labels and no domain-daemon session prompt
