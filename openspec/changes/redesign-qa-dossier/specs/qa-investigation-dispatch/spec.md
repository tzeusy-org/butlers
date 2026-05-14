## ADDED Requirements

### Requirement: Investigation Notes Artifact
The investigation agent SHALL emit a structured `investigation_notes` JSON artifact at terminal state. The artifact lives at `./.qa/investigation_notes.json` inside the worktree; the dispatcher reads, validates, and persists it before worktree teardown.

#### Scenario: Agent emits investigation notes JSON
- **WHEN** the investigation agent reaches a terminal step (commit complete or unfixable verdict)
- **THEN** it writes a JSON file at `./.qa/investigation_notes.json` inside its worktree
- **AND** the JSON conforms to the `InvestigationNotes` schema (`schema_version`, `headline`, `hypothesis`, `blurb_segments`, `claims`, `evidence_lines`, `counter_evidence`, `why_this_fix`, `diff_snapshot`)
- **AND** if the agent supports structured output (Claude), it uses structured-output mode to produce the JSON; otherwise it writes plain JSON matching the schema

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
QA SHALL not allow `evidence_lines[]` content (or any raw log content) to reach any GitHub-bound payload. The PR title, PR body, and any branch commit messages SHALL pass through `anonymize()` + `validate_anonymized()` before reaching `gh pr create` or `git commit -m`.

#### Scenario: PR pipeline cannot emit raw evidence lines
- **WHEN** the investigation agent has emitted `investigation_notes` and the dispatcher constructs the PR title and body
- **THEN** the PR title and body are composed from the anonymized fields the agent produced for that purpose (sanitized event summary, fingerprint reference, narrative summary), NOT from `evidence_lines[].msg`
- **AND** the PR title and body pass through `anonymize()` then `validate_anonymized()`
- **AND** `validate_anonymized()` failure deletes the remote branch and transitions the attempt to `anonymization_failed`

#### Scenario: Unit-level boundary test
- **WHEN** the test suite runs
- **THEN** `tests/core/qa/test_anonymization_boundary.py` asserts that every code path that constructs `gh pr create` arguments or `git commit -m` messages invokes `anonymize()` immediately prior, and that no such path takes a parameter whose name or type aliases to `evidence_lines`
