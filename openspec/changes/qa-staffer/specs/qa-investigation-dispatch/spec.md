# QA Investigation Dispatch

## Purpose

Unified investigation lifecycle management for all QA-originated issues regardless of discovery source. Creates worktrees, spawns LLM agents, monitors phase and workflow deadlines, creates anonymized PRs, and records outcomes. Subsumes and replaces the per-butler self-healing dispatch engine, preserving its 10-gate sequence within a single QA-owned pipeline. Investigation agents operate in sandboxed worktree environments with dedicated GitHub credentials from the secrets store, and the pipeline may chain multiple runtime sessions under one investigation.

## ADDED Requirements

### Requirement: Investigation Creation from QA Finding
The QA dispatcher SHALL create investigations for novel findings, using the existing `healing_attempts` table with a QA-specific source marker. All IDs SHALL be UUIDv7 for time-ordered sortability.

#### Scenario: Create investigation from finding
- **WHEN** a novel finding passes admission gates and is accepted for investigation
- **THEN** a row is inserted in `public.healing_attempts` with: `id` (UUIDv7), `fingerprint` matching the finding, `butler_name` matching the finding's `source_butler`, `status = "investigating"`, `severity` from the finding, `exception_type` and `call_site` from the finding, `sanitized_msg` from the finding's `event_summary`
- **AND** the row includes `qa_patrol_id` linking it to the originating patrol cycle
- **AND** the finding's `qa_findings.healing_attempt_id` is updated with the new attempt ID

#### Scenario: Concurrency cap enforcement
- **WHEN** the number of active investigations (status = "investigating") reaches `max_concurrent_investigations`
- **THEN** remaining novel findings are queued (not dispatched) for the next patrol cycle
- **AND** a dispatch-decision record is written with reason `concurrency_cap`
- **AND** a log INFO message indicates queued findings count

### Requirement: Gate Sequence Preservation
The QA dispatcher SHALL preserve the existing 10-gate dispatch sequence from self-healing, applied to each novel finding before investigation. Note: triage performs a fast dedup check (non-atomic) to filter obvious duplicates early; the dispatch gates perform the authoritative atomic claim. Cooldown appears in both layers intentionally — triage's check is a fast-path optimization, dispatch's is the atomic guarantee.

#### Scenario: Gates applied per-finding after triage
- **WHEN** a novel finding passes triage (fast dedup check)
- **THEN** the dispatcher applies the authoritative gate sequence: no-recursion guard (trigger_source), opt-in gate, fingerprint (already computed), severity gate, novelty gate (authoritative atomic claim — this is the authoritative check, not a duplicate of triage's fast check), cooldown gate, concurrency cap, circuit breaker, model resolution
- **AND** findings rejected by any gate are recorded with the rejection reason in `qa_findings.dedup_reason`
- **AND** any rejection before the first investigation session launches is tracked as a dispatch decision, not an execution failure

### Requirement: Gate Rejections Do Not Count as Execution Failures
QA admission-control outcomes SHALL remain distinct from launched investigation outcomes.

#### Scenario: Circuit breaker or cooldown rejection before launch
- **WHEN** a finding is rejected by cooldown, concurrency cap, circuit breaker, or no-model before any QA investigation session launches
- **THEN** no investigation attempt is marked `failed` solely because of that rejection
- **AND** the rejection does NOT contribute to the QA circuit-breaker failure streak
- **AND** the dashboard exposes it as a dispatch decision rather than a failed execution

### Requirement: Worktree-Based Investigation
Each investigation SHALL run in a dedicated git worktree branched off latest `main`, using shared worktree infrastructure.

#### Scenario: Worktree creation
- **WHEN** an investigation is dispatched
- **THEN** `git fetch origin main` is run first to ensure the latest `main`
- **AND** a worktree is created at `self-healing/qa/<fingerprint-prefix>-<timestamp>/`
- **AND** the branch name follows the pattern `qa/fix-<fingerprint-prefix>-<timestamp>`

#### Scenario: Worktree cleanup on completion
- **WHEN** an investigation completes (any terminal status)
- **THEN** the worktree is removed via `git worktree remove --force`
- **AND** the local branch is deleted if no PR was created

### Requirement: Investigation Agent Sandbox
Investigation agents SHALL operate in a sandboxed environment with minimal credentials and no access to butler runtime secrets.

#### Scenario: Agent environment
- **WHEN** the investigation agent is spawned in a worktree
- **THEN** the QA staffer resolves `BUTLERS_QA_GH_TOKEN` from `CredentialStore` and injects it as `GH_TOKEN` in the agent's environment (the `gh` CLI requires the env var name `GH_TOKEN` specifically)
- **AND** the agent's environment contains only: `GH_TOKEN`, `PATH`, and build-tool variables (`UV_CACHE_DIR`, etc.)
- **AND** it does NOT have: butler DB connection strings, API keys, OAuth tokens, user data, or any `BUTLERS_*` env vars
- **AND** it does NOT have MCP server connections (empty `mcp_servers` config)
- **AND** its filesystem scope is the worktree directory only

#### Scenario: GitHub credentials from secrets store
- **WHEN** the QA staffer needs to create a PR
- **THEN** it retrieves the GitHub token from the system secrets store at key `BUTLERS_QA_GH_TOKEN` (managed via the dashboard at /secrets)
- **AND** the token is scoped to: branch push + PR creation + PR labeling on `Tzeusy/butlers`
- **AND** the token SHALL NOT have merge or approve permissions — humans remain in the merge seat
- **AND** if the secret is not found, the investigation completes but transitions to `failed` with reason `"no_gh_token"`

### Requirement: QA Investigation Agent Prompt
The QA investigation agent SHALL receive a prompt that includes the error context from the discovery source, not from a live session. No raw logs or user data are included.

#### Scenario: Agent prompt composition
- **WHEN** the investigation agent is spawned
- **THEN** its prompt includes: error fingerprint, exception type, sanitized event summary, call site (module path), source butler name, occurrence count and time range, discovery source type, and instructions to: read relevant source code, identify root cause, implement a fix, run targeted tests, commit with a descriptive message
- **AND** the prompt explicitly instructs the agent to NOT include any user data, PII, or sensitive content in commits or PR descriptions

#### Scenario: Agent uses self_healing model tier
- **WHEN** the investigation agent is spawned
- **THEN** it uses `complexity = "self_healing"` for model resolution
- **AND** if no model is available in the self_healing tier, the investigation is skipped with status `failed` and reason `"no_model_available"`

#### Scenario: Agent context from reactive butler reports
- **WHEN** the finding originated from a `butler_reports` source (via `report_error`) with a non-empty `context` field
- **THEN** the agent's diagnostic reasoning is included in the investigation prompt (after anonymization)
- **AND** this gives the investigation agent a head start on diagnosis

### Requirement: Structured Evidence Payloads
QA findings and investigations SHALL carry structured evidence in addition to any free-form summary text.

#### Scenario: Runtime-derived finding includes structured evidence
- **WHEN** a finding originates from session records or runtime failures
- **THEN** the structured evidence includes available identifiers and diagnostics such as `session_id`, `request_id`, `trace_id`, `runtime_type`, `model`, redacted stderr excerpts, tool-call summaries, and source-specific metadata
- **AND** the investigation prompt references this evidence without embedding raw sensitive payloads

#### Scenario: Large evidence bundle is attached out-of-band
- **WHEN** the available diagnostic evidence is too large for the prompt
- **THEN** QA persists a redacted evidence artifact in the worktree
- **AND** the prompt points the agent to that artifact for detailed inspection

### Requirement: QA Self-Recursion Barrier
QA SHALL suppress autonomous investigation of failures originating from QA self-healing sessions.

#### Scenario: QA finding originated from QA self-healing session
- **WHEN** a finding's originating session belongs to the QA butler and was itself a self-healing/investigation session
- **THEN** normal autonomous investigation is suppressed
- **AND** the finding is routed to a QA meta-review/operator lane
- **AND** no standard QA investigation workflow is launched for that finding

### Requirement: Anonymized PR Pipeline
Investigation agents SHALL create PRs through the anonymization pipeline, ensuring no sensitive data reaches the public GitHub repository. **All personal details and sensitive data MUST be anonymized.**

#### Scenario: PR creation with anonymization
- **WHEN** the investigation agent has committed fixes
- **THEN** the branch is pushed to origin
- **AND** the PR title and body are passed through `anonymize()` and `validate_anonymized()`
- **AND** the PR is created via `gh pr create` with labels `["self-healing", "automated"]`
- **AND** the PR body includes: root cause analysis, affected butler(s), fix summary, patrol cycle reference (patrol ID, not raw log content), and a note that it was auto-generated by the QA staffer

#### Scenario: Anonymization validation failure
- **WHEN** `validate_anonymized()` detects residual PII in PR content
- **THEN** the remote branch is deleted
- **AND** the investigation transitions to `anonymization_failed`
- **AND** the error is logged with the validation failure reasons (locally only, not pushed)

#### Scenario: PR description links back to dashboard
- **WHEN** a PR is created and `[modules.qa].dashboard_base_url` is configured
- **THEN** the PR body includes a link to the investigation detail page: `<dashboard_base_url>/qa/investigations/<attempt_id>`
- **AND** if `dashboard_base_url` is not configured, the link is omitted (the dashboard may be on a private tailnet and the link would leak the hostname to a public PR)

### Requirement: Phased Investigation Workflow
Each QA investigation MAY span multiple runtime sessions under one overall investigation deadline.

#### Scenario: Diagnose, implement, and verify use separate sessions
- **WHEN** QA investigates a finding that requires diagnosis, code changes, and verification
- **THEN** it MAY run separate `diagnose`, `implement`, and `verify` sessions
- **AND** each session uses its own per-session timeout budget
- **AND** the investigation remains open across phases until it reaches a terminal result or the overall deadline expires

### Requirement: Investigation Timeout Watchdog
QA SHALL enforce both per-session timeouts and an overall investigation hard limit.

#### Scenario: Individual phase session exceeds timeout
- **WHEN** an investigation phase session runs longer than its configured session timeout
- **THEN** that phase session is cancelled
- **AND** QA records the phase timeout in investigation tracking
- **AND** the overall investigation remains governed by its remaining deadline budget

#### Scenario: Overall investigation deadline exceeded
- **WHEN** the total investigation runtime exceeds the configured hard limit (default: 60 minutes)
- **THEN** the investigation transitions to `timeout`
- **AND** any active phase session is cancelled
- **AND** the worktree is cleaned up

### Requirement: Investigation Outcome Recording
All investigation outcomes SHALL be recorded for dashboard reporting, PR tracking, and trend analysis. All record IDs SHALL be UUIDv7.

#### Scenario: Successful investigation with PR
- **WHEN** the investigation agent creates a PR
- **THEN** the `healing_attempts` row is updated: `status = "pr_open"`, `pr_url`, `pr_number`, `branch_name`
- **AND** the `closed_at` timestamp is set

#### Scenario: PR status tracking
- **WHEN** a healing attempt has `status = "pr_open"`
- **THEN** the QA staffer periodically checks PR status via `gh pr view --json state` (on each patrol cycle)
- **AND** this runs in the QA staffer daemon context (not in an agent worktree), using `GH_TOKEN` resolved from `CredentialStore.resolve("BUTLERS_QA_GH_TOKEN")`
- **AND** if the PR is merged, status transitions to `pr_merged`
- **AND** if the PR is closed without merge, status transitions to `failed` with `error_detail = "pr_closed_without_merge"`

#### Scenario: Failed investigation
- **WHEN** the investigation agent fails (tests don't pass, no fix found, crash)
- **THEN** the `healing_attempts` row is updated: `status = "failed"`, `error_detail` with sanitized failure reason
- **AND** the `closed_at` timestamp is set

#### Scenario: Agent determines issue is unfixable
- **WHEN** the investigation agent concludes the issue cannot be automatically fixed
- **THEN** the `healing_attempts` row is updated: `status = "unfixable"`, `error_detail` with the agent's reasoning
- **AND** the `closed_at` timestamp is set
