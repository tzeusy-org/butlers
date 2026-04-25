## MODIFIED Requirements

### Requirement: Multi-Runtime Adapter Support
The spawner delegates to a `RuntimeAdapter` abstract base class. Four concrete adapters are registered: `claude` (ClaudeCodeAdapter via subprocess), `codex` (CodexAdapter via subprocess), `gemini` (GeminiAdapter via subprocess), and `opencode` (OpenCodeAdapter via subprocess). Each adapter implements `invoke()`, `build_config_file()`, `parse_system_prompt_file()`, `binary_name`, `create_worker()`, and `reset()`.

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
- **THEN** the CodexAdapter runs `codex exec --json --full-auto` as an async subprocess
- **AND** embeds the system prompt into the initial prompt payload (Codex has no system prompt flag)
- **AND** parses JSON-lines output for result text, tool calls, and usage metrics

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
