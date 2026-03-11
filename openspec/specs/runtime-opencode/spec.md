# OpenCode Runtime Adapter

## Purpose
Runtime adapter for invoking OpenCode CLI as a butler's AI runtime, supporting multi-provider model selection, temporary config generation, and JSON output parsing.

## Requirements

### Requirement: OpenCode CLI Invocation
The `OpenCodeAdapter` SHALL invoke the OpenCode CLI via `opencode run --format json` as an async subprocess. The adapter SHALL locate the `opencode` binary on PATH via `shutil.which()` and raise `FileNotFoundError` if not found.

#### Scenario: Successful invocation
- **WHEN** the adapter invokes OpenCode with a valid prompt and config
- **THEN** it runs `opencode run --format json --model <model> <prompt>` as an async subprocess
- **AND** captures stdout/stderr and parses the JSON output

#### Scenario: Binary not found
- **WHEN** the `opencode` binary is not on PATH
- **THEN** the adapter raises `FileNotFoundError` with an install hint (`npm install -g opencode-ai`)

#### Scenario: Timeout exceeded
- **WHEN** the OpenCode process exceeds the configured timeout (default 300s)
- **THEN** the adapter kills the process and raises `TimeoutError`

#### Scenario: Non-zero exit code
- **WHEN** the OpenCode process exits with a non-zero return code
- **THEN** the adapter raises `RuntimeError` with the stderr/stdout error detail

### Requirement: Model Selection
The adapter SHALL pass the model via the `--model` CLI flag using OpenCode's `provider/model` format (e.g., `anthropic/claude-sonnet-4-5`). Butler authors MUST use the `provider/model` format in `butler.toml` when using the OpenCode runtime.

#### Scenario: Model passed via flag
- **WHEN** a model string is provided to `invoke()`
- **THEN** the command includes `--model <model>` before the prompt

#### Scenario: No model specified
- **WHEN** no model is provided
- **THEN** the `--model` flag is omitted and OpenCode uses its configured default

### Requirement: Temporary Config File Generation
The adapter SHALL write a temporary `opencode.jsonc` file per invocation containing MCP server config, instructions reference, and permission settings. The config path SHALL be passed via the `OPENCODE_CONFIG` environment variable.

#### Scenario: Config file structure
- **WHEN** the adapter prepares an invocation
- **THEN** it writes a JSONC config file with keys: `mcp` (server configs), `instructions` (system prompt file path), and `permission` (empty object for auto-mode)

#### Scenario: Config cleanup
- **WHEN** the invocation completes (success or failure)
- **THEN** the temporary config directory and all files within it are cleaned up

### Requirement: MCP Server Configuration
The adapter SHALL map butler MCP servers to OpenCode's `remote` server type in the config file. Each server entry SHALL include `type: "remote"`, `url`, and `enabled: true`.

#### Scenario: Single MCP server mapping
- **WHEN** the butler has one MCP server named `"my-butler"` with URL `"http://localhost:8080/mcp"`
- **THEN** the config's `mcp` section contains `"my-butler": {"type": "remote", "url": "http://localhost:8080/mcp", "enabled": true}`

#### Scenario: Multiple MCP servers
- **WHEN** multiple MCP servers are configured
- **THEN** each is mapped to a separate `remote` entry in the `mcp` section

#### Scenario: Invalid server config skipped
- **WHEN** a server config is not a dict or has no `url` key
- **THEN** it is skipped with a warning log

### Requirement: System Prompt Handling
The adapter SHALL read the system prompt from `OPENCODE.md` in the butler's config directory, falling back to `AGENTS.md`. The system prompt SHALL be written to a temporary file and referenced in the config's `instructions` array.

#### Scenario: OPENCODE.md present
- **WHEN** `OPENCODE.md` exists in the config directory
- **THEN** its contents are used as the system prompt

#### Scenario: Fallback to AGENTS.md
- **WHEN** `OPENCODE.md` is missing or empty but `AGENTS.md` exists
- **THEN** `AGENTS.md` contents are used as the system prompt

#### Scenario: No system prompt file
- **WHEN** neither `OPENCODE.md` nor `AGENTS.md` exists
- **THEN** an empty string is returned and no `instructions` entry is written

### Requirement: JSON Output Parsing
The adapter SHALL parse OpenCode's `--format json` output to extract result text, tool calls, and token usage. The parser SHALL handle multiple JSON event shapes and fall back to plain text if no valid JSON is found.

#### Scenario: Text result extraction
- **WHEN** the output contains message events with text content
- **THEN** the text parts are concatenated as the result string

#### Scenario: Tool call extraction
- **WHEN** the output contains tool use events (MCP tool calls, function calls)
- **THEN** each is normalized to `{"id": ..., "name": ..., "input": ...}` format

#### Scenario: Usage extraction
- **WHEN** the output contains token usage events
- **THEN** `input_tokens` and `output_tokens` are extracted into the usage dict

#### Scenario: Plain text fallback
- **WHEN** no valid JSON is found in stdout
- **THEN** the entire stdout is returned as result text

### Requirement: Environment Variable Handling
The adapter SHALL pass the provided `env` dict to the subprocess, adding `OPENCODE_CONFIG` pointing to the temporary config file. The adapter SHALL NOT filter out any env vars, since OpenCode supports multiple providers.

#### Scenario: Config env var injected
- **WHEN** the adapter prepares the subprocess environment
- **THEN** `OPENCODE_CONFIG` is set to the path of the temporary config file
- **AND** all other env vars from the caller are passed through

### Requirement: Runtime Args Support
The adapter SHALL support additional CLI arguments via the `runtime_args` parameter, appended to the command before the prompt.

#### Scenario: Custom args passed
- **WHEN** `runtime_args=["--agent", "plan"]` is provided
- **THEN** the command includes `--agent plan` before the prompt

### Requirement: Config File Builder
The `build_config_file()` method SHALL write an `opencode.jsonc` file with `mcpServers`-compatible config to the provided temporary directory.

#### Scenario: Config file written
- **WHEN** `build_config_file()` is called with MCP servers
- **THEN** an `opencode.jsonc` file is written with the server configurations

### Requirement: Worker Creation
The `create_worker()` method SHALL return a new `OpenCodeAdapter` instance preserving the binary path configuration.

#### Scenario: Worker is independent
- **WHEN** `create_worker()` is called
- **THEN** a new `OpenCodeAdapter` instance is returned with the same binary path setting

### Requirement: Adapter Registration
The adapter SHALL be registered as `"opencode"` in the runtime adapter registry at module import time.

#### Scenario: Registry lookup
- **WHEN** `get_adapter("opencode")` is called
- **THEN** it returns the `OpenCodeAdapter` class
