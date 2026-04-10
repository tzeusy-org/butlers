# QA Hardening Epic — Spec-to-Code Reconciliation (gen-1)

**Epic:** bu-0025a — Harden QA investigation to PR reliability
**Date:** 2026-04-11
**Reconciliation bead:** bu-0025a.7
**Status:** Complete — all-covered, no gaps found

---

## 1. Epic Acceptance Criteria Mapping

| # | Acceptance Criterion | Implementing Bead(s) | PR(s) | Status |
|---|---|---|---|---|
| 1 | QA investigation sessions execute deterministically without unintended retry re-execution | bu-0025a.1 | #1043 | COVERED |
| 2 | Successful PR landing always records durable PR identity; no-op outputs classified explicitly | bu-0025a.2 | #1046 | COVERED |
| 3 | Review follow-up can authenticate branch prep, start from remote head, persist retry outcome state | bu-0025a.3, bu-0025a.4 | #1042, #1047 | COVERED |
| 4 | QA investigation branches base from fresh origin/main when available | bu-0025a.5 | #1044 | COVERED |
| 5 | Session diagnostics are sufficient to debug failed QA investigations and follow-ups | bu-0025a.6 | #1050 | COVERED |
| 6 | Final reconciliation bead verifies the full investigation → PR flow end to end | bu-0025a.7 | this PR | COVERED |

---

## 2. Six-Gap Implementation Verification

### Gap 1: Codex MCP retry can re-execute QA sessions in the same worktree

**Bead:** bu-0025a.1 (PR #1043)

Key evidence in `origin/main`:

- `src/butlers/core/spawner.py:1332–1333` — `trigger_source in ("healing", "qa")` now passes `mcp_servers = {}` to `runtime.invoke()`.
- `src/butlers/core/runtimes/codex.py:680` — Codex retry condition: `mcp_failed = mcp_servers and not has_mcp_calls`. With `mcp_servers = {}` the condition is always `False` for QA sessions; the retry branch is never entered.
- Tests: `TestQaMcpGating` (3 methods) in `tests/core/test_spawner_mcp_config.py`; `test_qa_context_single_execution_bash_only` in `tests/adapters/test_codex_adapter.py`.

**FULLY COVERED**

---

### Gap 2: Initial PR creation can strand attempts in pr_open without durable PR identity; leaks no-op branches

**Bead:** bu-0025a.2 (PR #1046)

Key evidence in `origin/main`:

- `src/butlers/core/qa/dispatch.py:382–410` — `_detect_no_op_branch()` checks commits ahead of main before push; returns `no_op_branch` error code when there are no code changes.
- `src/butlers/core/qa/dispatch.py:529,536` — No-op detection fires as Step 0.5 before git push.
- `src/butlers/core/qa/dispatch.py:414–466` — `_resolve_pr_by_head()` looks up open PR by head branch name when `gh pr create` stdout cannot be parsed.
- `src/butlers/core/qa/dispatch.py:662,667,712–723` — `gh_pr_create_failed` prefix normalized; `pr_number` always resolved via fallback lookup.
- `src/butlers/core/qa/dispatch.py:1382–1404` — `check_open_pr_statuses` repairs `pr_number=NULL` via head-branch lookup instead of silently skipping.
- Tests: `test_detect_no_op_branch_*` (3 variants), `test_run_investigation_session_no_op_branch_marks_unfixable`, `test_create_qa_pr_pr_number_fallback_on_non_canonical_stdout`, `test_check_open_pr_statuses_repairs_missing_pr_number`, `test_check_open_pr_statuses_transitions_to_failed_when_repair_fails`.

**FULLY COVERED**

---

### Gap 3: Review follow-up branch prep lacks git auth and can reuse stale local branch refs

**Bead:** bu-0025a.3 (PR #1042)

Key evidence in `origin/main`:

- `src/butlers/core/qa/dispatch.py:1803` — `git_prep_env = build_git_auth_env(gh_token)` created for all branch-prep subprocesses.
- `src/butlers/core/qa/dispatch.py:1805–1815` — `_run_git_here()` inner helper passes `env=git_prep_env` to every prep command (fetch, worktree add).
- `src/butlers/core/qa/dispatch.py:1830–1842` — `git worktree add -B <branch> <path> origin/<branch>` anchors to remote head, preventing stale local branch reuse.
- Tests: `test_dispatch_pr_review_followup_branch_prep_uses_auth_env` and stale-local-branch prevention tests in `tests/core/qa/test_dispatch.py`.

**FULLY COVERED**

---

### Gap 4: Review follow-up rate limiting is lifetime-scoped, not per patrol cycle; failed follow-ups burn the only retry slot

**Bead:** bu-0025a.4 (PR #1047)

Key evidence in `origin/main`:

- `src/butlers/core/qa/dispatch.py:1302` — `patrol_id` threaded into `check_open_pr_statuses`.
- `src/butlers/core/qa/dispatch.py:1355–1380` — Queries `follow_up_cycle_patrol_id` and `follow_up_cycle_count` from `healing_attempts`.
- `src/butlers/core/qa/dispatch.py:1557–1566` — Gate uses cycle-scoped counter that resets when `patrol_id` changes; falls back to lifetime `follow_up_count` for backward-compatible standalone callers.
- `src/butlers/core/qa/dispatch.py:1855–1872` — Persists `follow_up_cycle_patrol_id`, `follow_up_cycle_count`, `last_follow_up_status`, `last_follow_up_at` on every outcome.
- Tests: `test_check_open_pr_statuses_cycle_reset_allows_followup`, `test_check_open_pr_statuses_same_cycle_blocks_second_followup`, `test_dispatch_pr_review_followup_persists_cycle_counter`.

**FULLY COVERED**

---

### Gap 5: QA investigation branches are created from local main even after fetching origin/main

**Bead:** bu-0025a.5 (PR #1044)

Key evidence in `origin/main`:

- `src/butlers/core/healing/worktree.py:218,222–242` — `create_healing_worktree` accepts `base_ref: str | None = None`; branches from `base_ref` when provided, falls back to local `"main"`.
- `src/butlers/core/qa/dispatch.py:2477,2495,2502,2510` — QA dispatch tracks fetch success (`_fetch_ok`); passes `base_ref="origin/main"` when fetch succeeds, falls back to `"main"` with a warning when unavailable.
- Tests: `test_dispatch_qa_uses_origin_main_after_successful_fetch` and `base_ref` contract tests.

**FULLY COVERED**

---

### Gap 6: QA session diagnostics drop retry provenance and failure tool-call history, blocking forensics

**Bead:** bu-0025a.6 (PR #1050)

Key evidence in `origin/main`:

- `src/butlers/core/session_process_logs.py:25,34–37` — `write()` accepts `retry_attempted`, `retry_succeeded`, `result_source`, `attempt_count`.
- `src/butlers/core/runtimes/codex.py:721–727,735` — Codex adapter sets all four provenance fields on `_last_process_info` after every run; `result_source` = `"retry"` or `"first"` based on which subprocess result was used.
- `src/butlers/core/spawner.py:1431–1434,1563–1566` — Both the success and failure paths forward all four retry provenance fields to `session_process_log_write`.
- `src/butlers/core/spawner.py:1521,1545` — Failure path: `captured_on_failure = consume_runtime_session_tool_calls(runtime_session_id)` then `tool_calls=captured_on_failure`; no longer writes `tool_calls=[]` on failure.
- `src/butlers/api/models/session.py:24–27` — Session detail API exposes all four new diagnostic fields.

**FULLY COVERED**

---

## 3. Companion Beads (Beyond the Six Core Gaps)

These beads addressed reliability and quality issues discovered alongside the core gap work. All are closed with merged PRs.

| Bead | Title | PR | Scope |
|---|---|---|---|
| bu-0025a.8 | Canonicalize report_finding fingerprint and severity | #1045 | Dedup fragmentation; severity inflation/suppression; DB constraint violations |
| bu-0025a.9 | Prevent log-scanner starvation under noisy load | #1048 | Entry budget; file scan order randomization; configurable caps |
| bu-0025a.10 | Queue novel QA findings skipped by concurrency cap | #1049 | `dispatch_queued` column + `get_dispatch_queued_findings`; patrol body preloads backlog |
| bu-0025a.11 | Reconcile QA doctrine and spec contracts | #1051 | architecture.md, MANIFESTO.md, design.md, 3 OpenSpec files updated |
| bu-0025a.12 | Wire QA provenance and dedup linkage | #1054 | `trigger_source` end-to-end; `linked_attempt_id` for active-investigation dedup |
| bu-0025a.13 | Align QA structured evidence with contract | #1055 | Phase-1 structured evidence: session_records, log_scanner, prompts |

---

## 4. Discovered Follow-ups

None. No regressions or new gaps were identified.

---

## 5. Conclusion

The gen-1 QA hardening epic (bu-0025a) is **complete**. All six validated reliability gaps have implementations verified in `origin/main`. All 12 sibling beads are closed. No follow-up work is required to close coverage gaps. No gen-2 reconciliation bead is needed.
