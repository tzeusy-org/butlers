## MODIFIED Requirements

### Requirement: Temporary Config File Generation
The adapter SHALL write a temporary `opencode.jsonc` file per invocation containing MCP server config, instructions reference, and permission settings. The config path SHALL be passed via the `OPENCODE_CONFIG` environment variable. For a goal invocation the `permission` settings SHALL deny every OpenCode built-in tool (shell, file read and edit, web fetch and any other local tool) and SHALL NOT enable auto-mode or any other permission bypass, because the runtime's deny-all tool settings plus a broker-only MCP config replace permission-bypass flags. For an RFC 0038 D18 model function (such as discretion screening or transcription) the config SHALL contain no `mcp` servers and SHALL deny every built-in tool, so the call is tool-less; an adapter that cannot produce that configuration SHALL refuse to run a model function.

Source: RFC 0038 D3 and D18
Scope: v1-mandatory

#### Scenario: Config file structure
- **WHEN** the adapter prepares an invocation
- **THEN** it writes a JSONC config file with keys: `mcp` (server configs), `instructions` (system prompt file path), and `permission`

#### Scenario: A model function runs tool-less on OpenCode
- **WHEN** the adapter prepares a discretion-screening model function call
- **THEN** the config has an empty `mcp` section and a `permission` section that denies every built-in tool
- **AND** the call can return only its structured output

#### Scenario: Goal invocation denies built-in tools
- **WHEN** the adapter prepares a lead or child invocation
- **THEN** the `permission` settings deny every OpenCode built-in tool
- **AND** the config enables no auto-mode or permission bypass

#### Scenario: Config cleanup
- **WHEN** the invocation completes (success or failure)
- **THEN** the temporary config directory and all files within it are cleaned up

### Requirement: MCP Server Configuration
The adapter SHALL map butler MCP servers to OpenCode's `remote` server type in the config file. Each server entry SHALL include `type: "remote"`, `url`, and `enabled: true`. For a goal invocation the config SHALL name exactly one MCP server, the broker's goal endpoint carrying that invocation's bearer token, and SHALL NOT name any butler MCP server or local MCP server.

Source: RFC 0038 D1, D3, D7
Scope: v1-mandatory

#### Scenario: Goal invocation names only the broker
- **WHEN** the adapter prepares a lead or child invocation
- **THEN** the config's `mcp` section contains exactly one `remote` entry pointing at the broker goal endpoint with the invocation's bearer token
- **AND** no domain butler MCP URL appears in the config

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
The adapter SHALL read the system prompt from `OPENCODE.md` in the butler's config directory, falling back to `AGENTS.md`. The system prompt SHALL be written to a temporary file and referenced in the config's `instructions` array. For a goal invocation the adapter SHALL instead use only the governing text the goal runner supplies (the versioned lead policy and owner profile for a lead, the brief for a child) and SHALL NOT read any butler config directory, because the lead is a role rather than a butler and domain guidance reaches an invocation only through `guidance.load`.

Source: RFC 0038 D1, D2, D9
Scope: v1-mandatory

#### Scenario: Goal invocation uses runner-supplied governing text
- **WHEN** the adapter prepares a lead or child invocation
- **THEN** the `instructions` entry carries only the governing text supplied by the goal runner
- **AND** no `OPENCODE.md`, `AGENTS.md` or `CLAUDE.md` from a butler config directory is read

#### Scenario: OPENCODE.md present
- **WHEN** `OPENCODE.md` exists in the config directory
- **THEN** its contents are used as the system prompt

#### Scenario: Fallback to AGENTS.md
- **WHEN** `OPENCODE.md` is missing or empty but `AGENTS.md` exists
- **THEN** `AGENTS.md` contents are used as the system prompt

#### Scenario: No system prompt file
- **WHEN** neither `OPENCODE.md` nor `AGENTS.md` exists
- **THEN** an empty string is returned and no `instructions` entry is written

### Requirement: Environment Variable Handling
The adapter SHALL pass the provided `env` dict to the subprocess, adding `OPENCODE_CONFIG` pointing to the temporary config file. Outside goal invocations the adapter SHALL NOT filter out any env vars, since OpenCode supports multiple providers. For a goal invocation the subprocess environment SHALL hold only `PATH`, the invocation's staged model credential and `OPENCODE_CONFIG`, because an invocation never receives domain, database or connector credentials.

Source: RFC 0038 D3
Scope: v1-mandatory

#### Scenario: Config env var injected
- **WHEN** the adapter prepares the subprocess environment for an invocation outside goals
- **THEN** `OPENCODE_CONFIG` is set to the path of the temporary config file
- **AND** all other env vars from the caller are passed through

#### Scenario: Goal invocation environment is minimal
- **WHEN** the adapter prepares the subprocess environment for a lead or child invocation
- **THEN** the environment holds only `PATH`, the staged model credential and `OPENCODE_CONFIG`
- **AND** no database, connector or credential-store variable is present
