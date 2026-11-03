# Core Spawner Delta

## MODIFIED Requirements

### Requirement: Healing Session MCP Restriction
When spawning a healing agent session (`trigger_source = "healing"`), the spawner SHALL generate an empty MCP config. The healing agent has access to the codebase via the worktree and shell tools only.

#### Scenario: Healing session has no MCP servers
- **WHEN** the spawner builds the MCP config for a `trigger_source = "healing"` session
- **THEN** the `mcp_servers` dict is empty

#### Scenario: Healing session receives no GitHub credential
- **WHEN** the spawner builds the environment for a session carrying historical `trigger_source = "healing"` provenance
- **THEN** the env includes only `PATH` for tool discovery and trace-context propagation
- **AND** it includes no `GH_TOKEN` or other GitHub credential, even when one is resolvable from the credential store or host environment
- **AND** no other butler-specific credentials or env vars are passed
- **AND** QA publication remains only through the restricted QA publisher (REQ-qa-investigation-dispatch-001)
