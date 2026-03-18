# Self-Healing Dispatch

## Purpose

Shared decision engine that evaluates whether an error warrants spawning a healing agent. Used by BOTH the self-healing module (primary path — butler agent calls `report_error` MCP tool) and the spawner fallback (secondary path — hard crashes where the agent couldn't self-report). Applies a multi-gate check sequence and, if all gates pass, creates a worktree, spawns a healing agent, and starts a timeout watchdog.

## ADDED Requirements

### Requirement: Dual Entry Points
The dispatcher SHALL support two callers that provide error context in different shapes:

1. **Module path (primary)**: Called from the `report_error` MCP tool handler with structured agent-provided context (error_type, error_message, traceback string, call_site, agent reasoning, tool_name, severity_hint).
2. **Spawner fallback path (secondary)**: Called from the spawner's except block with a raw Python exception + traceback object, session_id, and trigger_source.

Both paths converge on the same gate checks and dispatch logic.

#### Scenario: Module path provides richer context
- **WHEN** dispatch is invoked from the `report_error` tool
- **THEN** the healing agent's prompt includes the butler's diagnostic reasoning from the `context` field (after anonymization)
- **AND** the agent's `severity_hint` is used if no automatic severity can be computed

#### Scenario: Spawner fallback provides raw exception
- **WHEN** dispatch is invoked from the spawner except block
- **THEN** fingerprinting uses `compute_fingerprint(exc, tb)` to extract structured fields from the raw exception
- **AND** no agent reasoning is available (the agent crashed before it could report)

#### Scenario: Both paths use identical gate logic
- **WHEN** dispatch is invoked from either path
- **THEN** the same gate sequence, tracking tables, worktree lifecycle, and PR flow are used

### Requirement: Dispatch Function Signature
The dispatcher SHALL expose a unified async function that accepts both structured and raw error inputs.

#### Scenario: Unified dispatch function
- **WHEN** `dispatch_healing()` is called
- **THEN** it accepts: `pool` (asyncpg.Pool), `butler_name` (str), `session_id` (UUID), `fingerprint_input` (either a `FingerprintResult` from module path or a `(exc, tb)` tuple from spawner path), `config` (healing config), `agent_context` (str, optional — agent reasoning from module path), `trigger_source` (str — for no-recursion check)

### Requirement: Gate Ordering
The dispatcher SHALL evaluate gates in the following strict order. Fingerprint computation is deferred until after the opt-in check, to avoid wasting CPU when healing is disabled.

1. **No-recursion guard** — reject if `trigger_source == "healing"`
2. **Opt-in gate** — reject if healing not enabled
3. **Fingerprint computation** — compute or accept pre-computed fingerprint
4. **Fingerprint persistence** — update session record (best-effort)
5. **Severity gate** — reject if severity below threshold
6. **Novelty gate** — reject if active attempt exists (atomic check+insert)
7. **Cooldown gate** — reject if recent terminal attempt within window
8. **Concurrency cap** — reject if too many active investigations
9. **Circuit breaker** — reject if consecutive failures exceed threshold
10. **Model resolution gate** — reject if no `self_healing` tier model available

#### Scenario: Opt-in checked before fingerprint computation
- **WHEN** healing is disabled and an error occurs
- **THEN** no fingerprint is computed, no DB queries are made, dispatch exits immediately

#### Scenario: All gates pass in order
- **WHEN** all 10 gates pass sequentially
- **THEN** a healing attempt is created and the healing agent is spawned

### Requirement: No Recursive Healing
Sessions with `trigger_source = "healing"` SHALL never enter the dispatch path. This is the FIRST check, before any other work.

#### Scenario: Healing session fails
- **WHEN** a healing agent session fails
- **THEN** dispatch is NOT invoked
- **AND** the failure is recorded in the `healing_attempts` row as status `failed`

### Requirement: Opt-In Gate
The dispatcher SHALL check `butler.toml` → `[modules.self_healing]` config or `[healing]` config for enabled state. If not enabled, dispatch is skipped.

#### Scenario: Healing disabled
- **WHEN** the self-healing module is not loaded or healing is disabled
- **THEN** dispatch returns without computing a fingerprint or checking any other gate

### Requirement: Fingerprint Update on Failed Session
After computing or receiving the fingerprint (gate 3), the dispatcher SHALL call `session_set_healing_fingerprint(pool, session_id, fingerprint)` to update the failed session's record. This is best-effort.

#### Scenario: Fingerprint written to session record
- **WHEN** the dispatcher has a fingerprint for a failed session
- **THEN** `session_set_healing_fingerprint()` is called
- **AND** the failed session's `healing_fingerprint` column is updated

#### Scenario: Fingerprint update failure is non-fatal
- **WHEN** `session_set_healing_fingerprint()` raises a database error
- **THEN** the error is logged at WARNING level and dispatch continues

### Requirement: Severity Gate
The dispatcher SHALL skip healing if the fingerprint's severity score is below the configured threshold. Lower numbers are MORE severe. Default threshold: `2` (medium).

#### Scenario: Error meets severity threshold
- **WHEN** severity is `1` (high) and threshold is `2`
- **THEN** severity gate passes (1 ≤ 2)

#### Scenario: Error below severity threshold
- **WHEN** severity is `3` (low) and threshold is `2`
- **THEN** severity gate fails and dispatch is skipped

### Requirement: Novelty Gate
The dispatcher SHALL check for active attempts matching the fingerprint. The check and creation MUST be atomic (see healing-session-tracking spec).

#### Scenario: First occurrence
- **WHEN** no active attempt exists for this fingerprint
- **THEN** novelty gate passes

#### Scenario: Already under investigation
- **WHEN** an `investigating` or `pr_open` attempt exists for this fingerprint
- **THEN** novelty gate fails and the session ID is appended to the existing attempt

#### Scenario: Module path returns status to caller
- **WHEN** dispatch is invoked from `report_error` and the novelty gate fails
- **THEN** the module returns `{"accepted": false, "reason": "already_investigating", ...}` to the butler agent

### Requirement: Cooldown Gate
Per-fingerprint cooldown applies uniformly to ALL terminal statuses. Default: 60 minutes.

#### Scenario: Within cooldown after any terminal status
- **WHEN** any terminal attempt for this fingerprint was closed within the cooldown window
- **THEN** cooldown gate fails

#### Scenario: Dashboard retry bypasses cooldown
- **WHEN** a retry is triggered via `POST /api/healing/attempts/{id}/retry`
- **THEN** cooldown is bypassed (explicit operator action)

### Requirement: Concurrency Cap
Global count of `investigating` rows MUST be less than `max_concurrent` (default: 2).

#### Scenario: At concurrency limit
- **WHEN** active investigations equal `max_concurrent`
- **THEN** concurrency gate fails

### Requirement: Circuit Breaker
Trips after N consecutive failure statuses (`failed`, `timeout`, `anonymization_failed`). `unfixable` is excluded. Resets on `pr_open`/`pr_merged` or manual dashboard reset.

#### Scenario: Circuit breaker trips
- **WHEN** last N terminal attempts are all failure statuses
- **THEN** all dispatch is halted

#### Scenario: Manual reset
- **WHEN** `POST /api/healing/circuit-breaker/reset` is called
- **THEN** circuit breaker clears

### Requirement: Model Resolution Gate
After all other gates pass, resolve a model from the `self_healing` tier. If none available, skip.

#### Scenario: No model available
- **WHEN** `resolve_model(butler_name, "self_healing")` returns `None`
- **THEN** dispatch logs WARNING and skips (no attempt row created)

#### Scenario: DB error during resolution
- **WHEN** `resolve_model()` raises a connection error
- **THEN** dispatch catches, logs WARNING, and skips

### Requirement: Healing Agent Spawning
After all gates pass, the dispatcher creates the worktree, inserts the attempt row atomically, and spawns the healing agent.

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

### Requirement: Healing Agent Timeout Watchdog
A watchdog `asyncio.Task` is created alongside the healing agent. If the session doesn't complete within `timeout_minutes` (default: 30), the watchdog cancels it.

#### Scenario: Exceeds timeout
- **WHEN** healing session is still running after timeout
- **THEN** session is cancelled, attempt transitions to `timeout`, worktree is cleaned up

### Requirement: PR Creation Flow
After the healing agent completes successfully, push branch, anonymize, validate, and create PR.

#### Scenario: Full PR flow
- **WHEN** healing agent has committed fixes
- **THEN** `git push origin <branch>` → anonymize PR content → validate → `gh pr create` with labels `self-healing` + `automated`

#### Scenario: Push failure
- **WHEN** `git push` fails
- **THEN** attempt transitions to `failed`, worktree cleaned up

#### Scenario: Anonymization blocks PR
- **WHEN** validation detects residual PII
- **THEN** remote branch deleted, attempt transitions to `anonymization_failed`

### Requirement: Semaphore Behavior for Healing Sessions
Healing sessions bypass the per-butler semaphore but acquire the global semaphore. This is critical for the module path where the calling session is still active.

#### Scenario: Module path — no deadlock
- **WHEN** a butler calls `report_error` during its session (holding the per-butler semaphore)
- **AND** the healing agent is spawned for the same butler
- **THEN** the healing session bypasses the per-butler semaphore and does not deadlock

#### Scenario: Healing respects global cap
- **WHEN** the global semaphore is fully occupied
- **THEN** the healing session queues behind the global semaphore

### Requirement: Dispatch Errors Are Non-Fatal
All dispatch errors (gate checks, worktree creation, agent spawn) SHALL be caught and logged. They MUST NOT propagate to the caller — neither the MCP tool handler nor the spawner except block.

#### Scenario: Module path error handling
- **WHEN** dispatch raises an unexpected exception during gate checks
- **THEN** the `report_error` tool returns `{"accepted": false, "reason": "internal_error", "message": "Self-healing dispatch failed"}`

#### Scenario: Spawner fallback error handling
- **WHEN** dispatch raises during spawner fallback
- **THEN** the exception is logged at WARNING and the original `SpawnerResult` is unaffected

### Requirement: Trace Isolation
The dispatcher SHALL create its own OpenTelemetry span, not inherit from the failed session.

#### Scenario: Independent trace span
- **WHEN** dispatch starts
- **THEN** a new root span `healing.dispatch` is created
- **AND** the failed session's `trace_id` is recorded as a span attribute
