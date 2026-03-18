# Self-Healing Module

## Purpose

A butler module (`modules/self_healing/`) that registers MCP tools on every butler's MCP server, providing the **primary** entry point for self-healing. When a butler agent encounters an unexpected error during a session, it calls the `report_error` tool with structured error context and its own diagnostic reasoning. The module handles fingerprinting, deduplication gate checks, and dispatching a healing agent — all within the MCP server process. This is architecturally superior to daemon-only detection because the butler agent has richer context (its reasoning, what it was trying to do, why it thinks the error occurred) than a bare exception + traceback.

## ADDED Requirements

### Requirement: Module Identity
The self-healing module SHALL implement the `Module` abstract base class with name `self_healing`, no module dependencies, and a Pydantic config schema.

#### Scenario: Module registration
- **WHEN** `ModuleRegistry.default_registry()` scans `butlers.modules`
- **THEN** the `self_healing` module is discovered and available for loading

#### Scenario: Module config schema
- **WHEN** `butler.toml` contains `[modules.self_healing]`
- **THEN** the module's config schema accepts: `enabled` (bool, default: true — module is loaded but dispatch gating uses `[healing]` config), `severity_threshold` (int, default: 2), `max_concurrent` (int, default: 2), `cooldown_minutes` (int, default: 60), `circuit_breaker_threshold` (int, default: 5), `timeout_minutes` (int, default: 30)

#### Scenario: No dependencies
- **WHEN** the module's `dependencies` property is checked
- **THEN** it returns an empty list (self-healing is standalone — no dependency on other modules)

### Requirement: report_error MCP Tool
The module SHALL register a `report_error` tool that butler agents call when they encounter unexpected exceptions or errors during their session. This is the primary entry point for self-healing.

#### Scenario: Tool registration
- **WHEN** `register_tools(mcp, config, db)` is called
- **THEN** the `report_error` tool is registered on the butler's MCP server

#### Scenario: Tool parameters
- **WHEN** a butler agent calls `report_error`
- **THEN** the tool accepts:
  - `error_type` (str, required) — fully qualified exception class name (e.g. `asyncpg.exceptions.UndefinedTableError`)
  - `error_message` (str, required) — the exception message
  - `traceback` (str, optional) — the formatted traceback string
  - `call_site` (str, optional) — file:function where the error occurred (agent's best guess)
  - `context` (str, optional) — the agent's reasoning about what went wrong, what it was trying to do, and what might fix it
  - `tool_name` (str, optional) — which MCP tool was being called when the error occurred
  - `severity_hint` (str, optional) — agent's assessment: `critical`, `high`, `medium`, `low`

#### Scenario: Successful error report
- **WHEN** `report_error` is called with valid parameters and all dispatch gates pass
- **THEN** the tool returns a JSON object: `{"accepted": true, "fingerprint": "<hex>", "attempt_id": "<uuid>", "message": "Healing agent dispatched"}`

#### Scenario: Error report deduplicated
- **WHEN** `report_error` is called with a fingerprint that already has an active investigation
- **THEN** the tool returns: `{"accepted": false, "fingerprint": "<hex>", "reason": "already_investigating", "attempt_id": "<existing-uuid>", "message": "This error is already under investigation"}`
- **AND** the current session's ID is appended to the existing attempt's `session_ids`

#### Scenario: Error report rejected by gate
- **WHEN** `report_error` is called but a dispatch gate fails (cooldown, concurrency, circuit breaker, no model)
- **THEN** the tool returns: `{"accepted": false, "fingerprint": "<hex>", "reason": "<gate_name>", "message": "<human-readable explanation>"}`

#### Scenario: Error report with agent context enriches healing prompt
- **WHEN** `report_error` is called with a non-empty `context` field
- **THEN** the agent's diagnostic reasoning is included in the healing agent's prompt (after anonymization)
- **AND** the original session prompt/output is still NOT included

### Requirement: get_healing_status MCP Tool
The module SHALL register a `get_healing_status` tool that butler agents can call to query the status of healing attempts.

#### Scenario: Tool registration
- **WHEN** `register_tools(mcp, config, db)` is called
- **THEN** the `get_healing_status` tool is registered on the butler's MCP server

#### Scenario: Query by fingerprint
- **WHEN** `get_healing_status(fingerprint="abc123...")` is called
- **THEN** it returns the most recent healing attempt for that fingerprint with all status fields

#### Scenario: Query recent attempts for this butler
- **WHEN** `get_healing_status()` is called with no arguments
- **THEN** it returns the 5 most recent healing attempts for this butler, ordered by `created_at DESC`

#### Scenario: No active attempts
- **WHEN** `get_healing_status()` is called and no healing attempts exist for this butler
- **THEN** it returns `{"attempts": [], "message": "No healing attempts found"}`

### Requirement: Tool Sensitivity Metadata
The module SHALL declare sensitivity metadata for its tools to integrate with the approvals module.

#### Scenario: report_error sensitivity
- **WHEN** the approvals module queries tool metadata for `report_error`
- **THEN** `error_message` and `traceback` are marked as sensitive (may contain PII from error context)
- **AND** `context` is marked as sensitive (may contain the agent's reasoning about user-related errors)

### Requirement: Module Startup and Shutdown
The module SHALL run recovery and cleanup on startup.

#### Scenario: Startup recovery
- **WHEN** `on_startup(config, db)` is called
- **THEN** the module runs `recover_stale_attempts(pool, timeout_minutes)` to clean up investigations left by a prior crash
- **AND** runs `reap_stale_worktrees(repo_root, pool)` to clean up orphaned worktrees

#### Scenario: Shutdown cleanup
- **WHEN** `on_shutdown()` is called
- **THEN** any in-progress timeout watchdog tasks are cancelled (best-effort)
- **AND** active healing attempts are NOT terminated (they may complete independently)

### Requirement: Module Delegates to Core Healing Package
The module SHALL NOT implement fingerprinting, dispatch logic, worktree management, or anonymization directly. It delegates all of these to the shared `src/butlers/core/healing/` package. This ensures the spawner fallback and the module use identical logic.

#### Scenario: Shared code path
- **WHEN** `report_error` is called
- **THEN** the module calls `compute_fingerprint()` from `core.healing.fingerprint`
- **AND** runs gate checks via functions from `core.healing.tracking`
- **AND** creates worktrees via `core.healing.worktree`
- **AND** constructs the healing agent prompt and spawns via `core.healing.dispatch`

### Requirement: Healing Agent Spawning from Module
When the module dispatches a healing agent, it SHALL spawn the agent as a new session on the SAME butler's spawner, with `trigger_source="healing"` and `complexity="self_healing"`. The healing session runs concurrently with the original session (which may still be active when the error is reported).

#### Scenario: Healing agent spawned while butler session is active
- **WHEN** a butler agent calls `report_error` during its session and dispatch gates pass
- **THEN** the healing agent is spawned as a separate session via the spawner
- **AND** the original butler session continues unblocked (the MCP tool returns immediately after dispatch)
- **AND** the healing session bypasses the per-butler semaphore (so it doesn't deadlock against the calling session)

#### Scenario: report_error is non-blocking
- **WHEN** a butler agent calls `report_error`
- **THEN** the tool returns within 1-2 seconds (gate checks + DB insert only)
- **AND** the actual healing agent spawn happens asynchronously after the tool returns
