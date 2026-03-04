## MODIFIED Requirements

### Requirement: Multi-Runtime Adapter Support
The spawner delegates to a `RuntimeAdapter` abstract base class. Four concrete adapters are registered: `claude-code` (ClaudeCodeAdapter via Claude Agent SDK), `codex` (CodexAdapter via subprocess), `gemini` (GeminiAdapter via subprocess), and `opencode` (OpenCodeAdapter via subprocess). Each adapter implements `invoke()`, `build_config_file()`, `parse_system_prompt_file()`, `binary_name`, `create_worker()`, and `reset()`.

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

#### Scenario: OpenCode adapter invocation
- **WHEN** the butler's runtime type is `opencode`
- **THEN** the OpenCodeAdapter runs `opencode run --format json` as an async subprocess
- **AND** writes a temporary JSONC config with MCP servers, instructions, and permissions
- **AND** parses JSON output for result text, tool calls, and usage metrics

#### Scenario: Unknown runtime type fails at config load
- **WHEN** `get_adapter(type_str)` is called with an unregistered runtime type
- **THEN** a `ValueError` is raised listing available adapters
