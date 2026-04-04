# Self-Healing Dispatch

## MODIFIED Requirements

### Requirement: Healing Agent Spawning
After all gates pass, the dispatcher creates the worktree, inserts the attempt row atomically, and spawns the healing agent. The dispatcher SHALL support an optional `qa_patrol_id` parameter for QA-originated investigations.

#### Scenario: Healing agent spawn parameters
- **WHEN** the dispatcher spawns a healing agent
- **THEN** `trigger()` is called with `complexity="self_healing"`, `trigger_source="healing"`, CWD=worktree path
- **AND** the `healing_attempts` row's `healing_session_id` is updated with the returned session ID

#### Scenario: Healing agent prompt includes agent context (module path)
- **WHEN** dispatch was invoked from `report_error` with a `context` field
- **THEN** the healing agent's prompt includes the anonymized agent reasoning
- **AND** this gives the healing agent a head start on diagnosis

#### Scenario: Healing agent prompt without agent context (spawner fallback)
- **WHEN** dispatch was invoked from the spawner fallback
- **THEN** the healing agent's prompt includes only: fingerprint, exception type, sanitized message, call site, butler name

#### Scenario: Healing agent does not receive MCP tools
- **WHEN** a healing agent session is spawned
- **THEN** the MCP config is empty (no `mcp_servers` entries)
- **AND** the agent has access to: codebase (via worktree), `git`, `uv`, `pytest`, `ruff`, `gh`

#### Scenario: Healing agent receives GitHub credentials
- **WHEN** a healing agent session is spawned
- **THEN** the env includes `GH_TOKEN` for `gh pr create`
- **AND** no other butler-specific credentials are passed

#### Scenario: QA-originated investigation
- **WHEN** the dispatcher is invoked with a non-null `qa_patrol_id`
- **THEN** the `healing_attempts` row includes `qa_patrol_id` linking it to the originating patrol cycle
- **AND** the healing agent's prompt includes QA-specific context: log-derived error summary, occurrence count, time range, and source butler

## ADDED Requirements

### Requirement: Shared Dispatch Infrastructure
The worktree management, anonymizer pipeline, and PR creation flow SHALL be extractable as shared utilities usable by both per-butler self-healing and QA staffer dispatch paths.

#### Scenario: Worktree creation is caller-agnostic
- **WHEN** `create_healing_worktree()` is called
- **THEN** it accepts a `prefix` parameter (default: `"self-healing"`, QA uses `"qa"`)
- **AND** the worktree path follows the pattern `{prefix}/{butler_name}/{fingerprint_prefix}-{timestamp}/`

#### Scenario: PR pipeline is caller-agnostic
- **WHEN** the PR creation flow runs
- **THEN** it accepts configurable `labels` (default: `["self-healing", "automated"]`)
- **AND** the anonymization and validation steps are identical regardless of caller

#### Scenario: healing_attempts table supports QA source marker
- **WHEN** a QA-originated investigation is recorded
- **THEN** the `healing_attempts` table includes a nullable `qa_patrol_id` column (UUID FK to `qa_patrols`)
- **AND** existing self-healing rows have `qa_patrol_id = NULL`
- **AND** QA-originated rows have `qa_patrol_id` set to the originating patrol's ID
