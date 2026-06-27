# QA Investigation Dispatch

## Purpose

Unified investigation lifecycle management for all QA-originated issues regardless of discovery source. Creates worktrees, spawns LLM agents, monitors phase and workflow deadlines, creates anonymized PRs, and records outcomes. Subsumes and replaces the per-butler self-healing dispatch engine, preserving its 10-gate sequence within a single QA-owned pipeline. Investigation agents operate in sandboxed worktree environments with dedicated GitHub credentials from the secrets store, and the pipeline may chain multiple runtime sessions under one investigation.

## Requirements

### Requirement: Investigation Creation from QA Finding
The QA dispatcher SHALL create investigations for novel findings, using the existing `healing_attempts` table with a QA-specific source marker. Journal-event and notes IDs SHALL be UUIDv7 for time-ordered sortability; the reused `healing_attempts.id` and `qa_findings.id` primary keys retain their legacy `gen_random_uuid()` (UUIDv4) defaults.

#### Scenario: Create investigation from finding
- **WHEN** a novel finding passes admission gates and is accepted for investigation
- **THEN** a row is inserted in `public.healing_attempts` with: `id` (UUIDv4, from the table's legacy `gen_random_uuid()` default), `fingerprint` matching the finding, `butler_name` matching the finding's `source_butler`, `status = "investigating"`, `severity` from the finding, `exception_type` and `call_site` from the finding, `sanitized_msg` from the finding's `event_summary`
- **AND** the row includes `qa_patrol_id` linking it to the originating patrol cycle
- **AND** the finding's `qa_findings.healing_attempt_id` is updated with the new attempt ID

#### Scenario: Concurrency cap enforcement
- **WHEN** the number of QA-originated active investigations (`count_active_attempts(pool, qa_only=True)`) reaches `max_concurrent_investigations`
- **THEN** remaining novel findings in this patrol cycle are skipped (not dispatched immediately)
- **AND** skipped findings are recorded with `dedup_reason = "concurrency_cap"` and `dispatch_queued = TRUE` in their `qa_findings` rows — this is a durable backlog that survives daemon restart
- **AND** at the start of each subsequent patrol cycle, `get_dispatch_queued_findings()` atomically fetches and clears queued rows (using `FOR UPDATE SKIP LOCKED`) and prepends them to the triage batch, giving cap-skipped findings a guaranteed future dispatch opportunity
- **AND** a log INFO message indicates skipped findings count

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
- **AND** a worktree is created under `.healing-worktrees/qa/<source_butler>/<fingerprint-prefix>-<epoch>/` via `create_healing_worktree(prefix="qa")` (the fingerprint prefix is the first 12 hex characters)
- **AND** the branch name follows the pattern `qa/<source_butler>/<fingerprint-prefix>-<epoch>`

#### Scenario: Worktree cleanup on completion
- **WHEN** an investigation completes (any terminal status)
- **THEN** the worktree is removed via `git worktree remove --force`
- **AND** the local branch is deleted if no PR was created

### Requirement: Investigation Agent Sandbox
Investigation agents SHALL operate in a sandboxed environment with minimal credentials and no access to butler runtime secrets.

#### Scenario: Agent environment
- **WHEN** the investigation agent is spawned in a worktree
- **THEN** the QA staffer resolves `BUTLERS_QA_GH_TOKEN` from `CredentialStore` and injects it as `GH_TOKEN` in the agent's environment (the `gh` CLI requires the env var name `GH_TOKEN` specifically)
- **AND** if configured, the QA staffer resolves `BUTLERS_QA_GIT_AUTHOR_NAME` and `BUTLERS_QA_GIT_AUTHOR_EMAIL` and injects them as `GIT_AUTHOR_*` / `GIT_COMMITTER_*` so non-interactive `git commit` does not depend on per-worktree `git config`
- **AND** the agent's environment contains only: `GH_TOKEN`, `PATH`, and build-tool variables (`UV_CACHE_DIR`, etc.)
- **AND** it does NOT have: butler DB connection strings, API keys, OAuth tokens, user data, or any `BUTLERS_*` env vars
- **AND** it does NOT have MCP server connections (the spawner automatically sets empty MCP server config when `trigger_source="qa"`, preventing access to live production state and suppressing the Codex adapter's MCP-discovery retry path)
- **AND** its filesystem scope is the worktree directory only

#### Scenario: Agent runs from a QA helper workspace
- **WHEN** the investigation agent is spawned in a worktree
- **THEN** its current working directory is a QA-owned helper subdirectory inside the worktree, not the repository root
- **AND** that helper directory contains a local `AGENTS.md` override that disables unrelated repo-level workflow instructions such as `bd` usage, generic session-close rules, or self-managed PR/push steps
- **AND** the helper directory exposes the repo roots needed for normal QA commands (`src/`, `tests/`, `roster/`, `frontend/`, `pyproject.toml`, `uv.lock`) so repo-relative validation commands still work unchanged

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
- **AND** the prompt explicitly instructs the agent to ignore unrelated repository workflow instructions and to not run `bd`, push branches manually, or open PRs itself

#### Scenario: Agent uses specialty model tier
- **WHEN** the investigation agent is spawned
- **THEN** it uses `Complexity.SPECIALTY` for model resolution (the legacy `self_healing` tier was retired in migration core_093 and is accepted only as a deprecated alias that remaps to `specialty`)
- **AND** if no model is available in the `specialty` tier, the investigation is skipped with status `failed` and reason `"no_model_available"`

#### Scenario: Agent context from reactive butler reports
- **WHEN** the finding originated from a `butler_reports` source (via `report_error`) with a non-empty `context` field
- **THEN** the agent's diagnostic reasoning is included in the investigation prompt (after anonymization)
- **AND** this gives the investigation agent a head start on diagnosis

### Requirement: Structured Evidence Payloads
QA findings and investigations SHALL carry structured evidence in addition to any free-form summary text.

The evidence set is bounded by what each discovery source exposes through its
sanctioned access path (RFC 0010).  The current implementation delivers Phase 1
evidence (identifiers and source-specific metadata available without additional
DB migrations).  Richer evidence fields (`request_id`, `trace_id`, `runtime_type`,
`model`, tool-call summaries) are deferred to a future Phase 2 that extends the
`v_qa_recent_failures` view.

#### Scenario: Session-records finding includes structured evidence (Phase 1)
- **WHEN** a finding originates from the `session_records` source
- **THEN** `structured_evidence` contains:
  - `source`: `"session_records"`
  - `status`: the session failure status (`"error"` | `"timeout"` | `"crash"`)
  - `session_ids`: a list of up to 5 session UUIDs (as strings) that share this fingerprint, drawn from the `v_qa_recent_failures` view
- **AND** the investigation prompt includes a `## Structured Evidence` section listing the available identifiers without embedding raw sensitive payloads

#### Scenario: Log-scanner finding includes structured evidence (Phase 1)
- **WHEN** a finding originates from the `log_scanner` source
- **THEN** `structured_evidence` contains:
  - `source`: `"log_scanner"`
  - `log_file`: the filename (stem) of the log file where the fingerprint was first seen
  - `level`: the log level of the first occurrence (e.g. `"error"`, `"critical"`)
  - `trigger_source`: the `trigger_source` field from the structured JSON log entry if present; omitted if absent
- **AND** the investigation prompt includes a `## Structured Evidence` section listing the available identifiers without embedding raw sensitive payloads

#### Scenario: Large evidence bundle attached out-of-band (Phase 2 — not yet implemented)
- **NOTE** Out-of-band worktree artifact persistence for large evidence bundles is deferred to Phase 2.
- **WHEN** the available diagnostic evidence is too large for the prompt (Phase 2 only)
- **THEN** QA persists a redacted evidence artifact in the worktree
- **AND** the prompt points the agent to that artifact for detailed inspection

### Requirement: QA Self-Recursion Barrier
QA SHALL suppress autonomous investigation of failures originating from QA self-healing sessions. The suppression decision is driven by a `source_session_trigger_source` field carried on every `QaFinding`.

#### Scenario: QaFinding carries source session trigger_source
- **WHEN** a discovery source produces a `QaFinding`
- **THEN** the finding MUST include `source_session_trigger_source` (nullable str): the `trigger_source` value from the session record or log entry that produced the error
- **AND** for `session_records` source: `source_session_trigger_source` is read directly from the session row's `trigger_source` column
- **AND** for `log_scanner` source: `source_session_trigger_source` is extracted from the structured JSON log field `trigger_source` if present; `null` if absent
- **AND** for `butler_reports` source: `source_session_trigger_source` is derived from the source_butler's active session context at the time of `report_finding`; the `report_finding` tool SHALL accept an optional `trigger_source` parameter and include it in the buffered finding
- **AND** `source_session_trigger_source` is stored in the `qa_findings` row for auditing

#### Scenario: QA finding originated from QA self-healing session
- **WHEN** a finding's `source_butler == "qa"` AND `source_session_trigger_source` is in `{"healing", "qa"}` (QA investigation sessions use `trigger_source="qa"`; healing sessions use `trigger_source="healing"`)
- **THEN** normal autonomous investigation is suppressed
- **AND** the finding is routed to a QA meta-review/operator lane (visible at `GET /api/qa/meta-review`)
- **AND** no standard QA investigation workflow is launched for that finding

#### Scenario: QA finding with unknown trigger source
- **WHEN** a finding's `source_butler == "qa"` AND `source_session_trigger_source` is null or not in `{"healing", "qa"}`
- **THEN** the finding is treated as potentially recursive and routed to the meta-review/operator lane as a precaution
- **AND** a log WARNING is emitted: "QA finding from QA butler with unrecognized trigger_source; routing to meta-review"

#### Scenario: Non-QA finding is never suppressed by this barrier
- **WHEN** a finding's `source_butler` is any value other than `"qa"`
- **THEN** the self-recursion barrier does NOT apply regardless of `source_session_trigger_source`

### Requirement: Anonymized PR Pipeline
Investigation agents SHALL create PRs through the anonymization pipeline, ensuring no sensitive data reaches the public GitHub repository. **All personal details and sensitive data MUST be anonymized.**

#### Scenario: PR creation with anonymization
- **WHEN** the investigation agent has committed fixes
- **THEN** the branch is pushed to origin
- **AND** the PR title and body are passed through `anonymize()` and `validate_anonymized()`
- **AND** the PR labels are passed through the same `anonymize()` + `validate_anonymized()` gate (labels are externally visible on the public destination)
- **AND** the PR is created via `gh pr create` with the sanitized labels (default `["self-healing", "automated"]`)
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
The QA investigation infrastructure SHALL support phase-session tracking for investigations. Phase sessions are recorded and tracked via `record_phase_session` and `update_phase_session_status`. The v1 implementation uses a single combined `investigate` phase; separate diagnose, implement, and verify phases are a future extension enabled by this infrastructure.

#### Scenario: Diagnose, implement, and verify use separate sessions
- **WHEN** QA investigates a finding that requires diagnosis, code changes, and verification
- **THEN** it SHALL record at least one phase session (currently a single `investigate` phase) to track the session with lineage for audit and recovery
- **AND** each session uses its own per-session timeout budget
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
All investigation outcomes SHALL be recorded for dashboard reporting, PR tracking, and trend analysis. Journal-event and notes record IDs SHALL be UUIDv7; the reused `healing_attempts.id` and `qa_findings.id` primary keys retain their legacy `gen_random_uuid()` (UUIDv4) defaults.

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

### Requirement: Investigation Notes Artifact
The investigation agent SHALL emit a structured `investigation_notes` JSON artifact at terminal state. The artifact lives at `./.qa/investigation_notes.json` inside the worktree; the dispatcher reads, validates, and persists it before worktree teardown.

#### Scenario: Agent emits investigation notes JSON
- **WHEN** the investigation agent reaches a terminal step (commit complete or unfixable verdict)
- **THEN** it writes a JSON file at `./.qa/investigation_notes.json` inside its worktree
- **AND** the JSON conforms to the `InvestigationNotes` schema (`schema_version`, `headline`, `hypothesis`, `blurb_segments`, `claims`, `evidence_lines`, `counter_evidence`, `why_this_fix`, `diff_snapshot`)
- **AND** the artifact is governed by the portable file contract: the agent writes plain JSON matching the schema, and the dispatcher validates the file after the runtime exits
- **AND** runtime-specific final-response structured-output modes are not required for this artifact unless the `RuntimeAdapter.invoke()` contract gains an explicit artifact-file schema channel

#### Scenario: Dispatcher reads and persists notes
- **WHEN** the agent signals completion and before worktree teardown
- **THEN** the dispatcher reads `./.qa/investigation_notes.json`, validates against `InvestigationNotes`, and persists the parsed payload into `qa_findings.structured_evidence.investigation_notes` for every finding linked to the attempt
- **AND** the `qa_investigation_notes_parse_total{status}` Prometheus counter is incremented with `status="ok"` when the artifact parses cleanly, `status="partial"` when best-effort extraction recovers some fields, and `status="failed"` when no usable fields are recoverable
- **AND** parsing failure does NOT cause the investigation to transition to `failed`; the agent's terminal state is preserved and the notes simply remain absent

#### Scenario: Notes fields are agent-authored (not re-anonymized internally)
- **WHEN** the dispatcher persists `investigation_notes`
- **THEN** the `evidence_lines[]` payload is written verbatim from the agent's emission, since the dashboard surface that consumes it is operator-only
- **AND** the dispatcher does NOT re-pass `evidence_lines[]` through `anonymize()` before storage
- **AND** this exception is bounded to the `evidence_lines[]` field — all other narrative fields are anonymized by the agent on emission and are safe for either internal or external surfaces

### Requirement: Commit-Time Diff Snapshot
The dispatcher SHALL capture a unified diff of the agent's commit at commit time and persist it as a structured line-kind sequence inside `investigation_notes.diff_snapshot`.

#### Scenario: Diff captured before worktree teardown
- **WHEN** the agent's final commit step succeeds and before the worktree is removed
- **THEN** the dispatcher runs `git -C <worktree> diff --no-color HEAD~1..HEAD --unified=3`
- **AND** the dispatcher parses the unified-diff output into a list of `{ kind, text }` objects where `kind ∈ {"meta", "+", "-", " "}` (meta covers `diff --git` and `@@` hunk headers and filename markers)
- **AND** the parsed snapshot is written into `qa_findings.structured_evidence.investigation_notes.diff_snapshot`

#### Scenario: Large diff truncation
- **WHEN** the diff snapshot exceeds 10 000 lines
- **THEN** the snapshot is truncated to the first 10 000 lines
- **AND** a final `{ kind: "meta", text: "... (truncated, N more lines)" }` marker is appended where `N` is the dropped-line count

#### Scenario: No commit (unfixable verdict)
- **WHEN** the agent terminates without producing a commit (e.g. status transitions to `unfixable`)
- **THEN** the dispatcher writes `diff_snapshot: []` (empty array)
- **AND** no `git diff` is invoked

### Requirement: Journal Event Emission — Dispatch
The dispatcher SHALL emit structured journal events into `public.qa_investigation_events` for every dispatch-owned step in a case's lifecycle.

#### Scenario: drafted event on PR creation
- **WHEN** an investigation creates a PR and transitions to `status = 'pr_open'`
- **THEN** the dispatcher inserts a `qa_investigation_events` row with `step = 'drafted'`, `attempt_id`, `text` summarizing the PR (`"PR #<number> · <branch>"`), and a `detail` containing `+<additions> / −<deletions> · <files_touched> files`
- **AND** the event's `ts` is the PR creation timestamp

#### Scenario: wait event on CI pending
- **WHEN** the PR status poller observes that a PR is `pr_open` with CI status `pending`
- **THEN** the poller inserts a `qa_investigation_events` row with `step = 'wait'`, `text` describing the wait state (`"CI · <N> checks pending"`), and a `detail` listing the pending check names if available
- **AND** wait events are de-duplicated: at most one wait event is recorded per attempt per patrol cycle

#### Scenario: merged event on PR merge
- **WHEN** the PR status poller observes that a PR has transitioned to `pr_merged`
- **THEN** the dispatcher inserts a `qa_investigation_events` row with `step = 'merged'`, `text = "CI green · merged"`, and `detail` describing the next-patrol outcome when available (`"next patrol clean · case closed"`)

#### Scenario: escalated event on unfixable terminal
- **WHEN** an attempt transitions to `status = 'unfixable'` OR `status = 'failed'` with an `error_detail` indicating human action is required
- **THEN** the dispatcher inserts a `qa_investigation_events` row with `step = 'escalated'`, `text` summarizing the reason, and a `detail` referencing the user-action surface (e.g., `"surfaced on /overview attention"`)

### Requirement: Raw Log Retention and Cleanup
QA SHALL purge raw log content from `qa_findings.structured_evidence.evidence_lines[]` on a documented schedule, while preserving the narrative payload indefinitely.

#### Scenario: Daily retention cleanup
- **WHEN** the daily QA cleanup job runs (configured by `[modules.qa].retention_cleanup_hour`, default 04:00 UTC)
- **THEN** for every `qa_findings` row whose linked `healing_attempts.closed_at` is non-null AND older than 14 days, OR whose own `created_at` is older than 30 days, the `evidence_lines[]` field is stripped from `structured_evidence.investigation_notes`
- **AND** all other narrative fields (`headline`, `hypothesis`, `why_this_fix`, `diff_snapshot`, `counter_evidence`, `blurb_segments`, `claims`) are preserved
- **AND** the `qa_findings_retention_purged_total` Prometheus counter is incremented by the number of rows updated in the run

#### Scenario: Non-terminal cases are exempt
- **WHEN** a `qa_findings` row's linked `healing_attempts` row has `closed_at IS NULL` (case is still in non-terminal state)
- **THEN** the row's `evidence_lines[]` is retained regardless of `created_at` age, until the case reaches a terminal state and 14 days have elapsed

#### Scenario: Cleanup job logs but does not crash on partial failure
- **WHEN** the cleanup job encounters a JSONB shape that does not match the documented `investigation_notes` schema
- **THEN** the job skips that row, logs a WARNING with the row id and the malformed-shape reason, and continues with the next row
- **AND** the job's overall result remains successful so long as at least one row was successfully cleaned

### Requirement: Anonymization-on-Egress Guarantee
QA SHALL not allow `evidence_lines[]` content (or any raw log content) to reach any GitHub-bound payload. The PR title, PR body, PR labels, and any branch commit messages SHALL pass through `anonymize()` + `validate_anonymized()` before reaching `gh pr create` or `git commit -m`. The gate runs unconditionally (every destination is treated as public).

#### Scenario: PR pipeline cannot emit raw evidence lines
- **WHEN** the investigation agent has emitted `investigation_notes` and the dispatcher constructs the PR title and body
- **THEN** the PR title and body are composed from the anonymized fields the agent produced for that purpose (sanitized event summary, fingerprint reference, narrative summary), NOT from `evidence_lines[].msg`
- **AND** the PR title and body pass through `anonymize()` then `validate_anonymized()`
- **AND** `validate_anonymized()` failure deletes the remote branch and transitions the attempt to `anonymization_failed`

#### Scenario: PR labels are sanitized fail-closed
- **WHEN** the dispatcher assembles the `--label` arguments for `gh pr create`
- **THEN** each label is scrubbed via `anonymize()` and checked by `validate_anonymized()` before `gh pr create` runs
- **AND** if any label still contains residual sensitive content, the flow blocks before `gh pr create`, deletes the just-pushed remote branch, and transitions the attempt to `anonymization_failed` (QA path also increments the anonymization-failure counter)
- **AND** only the sanitized labels reach the `gh pr create --label` arguments

#### Scenario: Unit-level boundary test
- **WHEN** the test suite runs
- **THEN** `tests/core/qa/test_anonymization_boundary.py` asserts that every code path that constructs `gh pr create` arguments or `git commit -m` messages invokes `anonymize()` immediately prior, and that no such path takes a parameter whose name or type aliases to `evidence_lines`
