## 0. Reconciliation (bu-6fguq)

This change was unarchived because the merged code diverged from the original
delta specs. Resolution direction chosen: **reconcile spec → as-built** (rewrite
the delta specs to describe shipped behavior), then sync into `openspec/specs/`
and archive. The risky behavioral rewrites the original proposal called for
(in-flight cancellation, seed-config/accessor HOT thresholds, healing exemption,
SIGTERM→grace→SIGKILL escalation, dashboard/telemetry surfacing) were NOT
implemented in code and are intentionally NOT being built under this change — if
wanted, they should be filed as fresh changes.

- [x] 0.1 Verify as-built guardrails: post-session checks over the merged
  tool-call list raising `RuntimeError` (`spawner.py` `_check_degenerate_tool_loop`
  / `_check_tool_call_budget` / `_check_token_budget`, call site ~2215-2229).
- [x] 0.2 Verify thresholds are module constants
  (`_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD = 6`, `_DEFAULT_MAX_TOOL_CALLS = 0`),
  not accessor HOT fields / no `RuntimeSeedConfig` fields / no `runtime_config`
  columns / no healing exemption.
- [x] 0.3 Verify the error markers are wired into the failover classifier
  (`failover_classifier.py` `_GUARDRAIL_MARKERS`).
- [x] 0.4 Verify `memory_entity_resolve` raises `ValueError` on null/empty/both-args
  and reserves `[]` for valid-no-match (`modules/memory/tools/entities.py:282-292`),
  using the `identifier` argument (legacy `name` alias).
- [x] 0.5 Verify OpenCode timeout does a single `proc.kill()` (no SIGTERM grace)
  and the SQLite migration bootstrap retry IS implemented (`runtimes/opencode.py`).
- [x] 0.6 Rewrite delta specs (`core-spawner`, `core-sessions`, `core-modules`,
  `module-memory`, `runtime-opencode`) to match as-built; update proposal.

## As-built status of the original task list

- [x] Spawner guardrail checks (degenerate loop / tool-call / token budget) —
  implemented as post-session checks.
- [x] Error taxonomy markers — embedded in the `RuntimeError` message, recognised
  by the failover classifier (not a bare-enum `session_complete(error=...)` path).
- [x] `memory_entity_resolve` raises on invalid input.
- [x] OpenCode SQLite migration bootstrap retry.
- [x] OpenCode timeout terminates the subprocess (single SIGKILL) and raises
  `TimeoutError`.
- [ ] **NOT built (out of scope for this reconciliation):** `RuntimeSeedConfig`
  threshold fields + `runtime_config` columns + accessor HOT plumbing; healing-
  session exemption; in-flight cancellation; SIGTERM→grace→SIGKILL escalation +
  SIGTERM-trap verification test; dashboard surfacing of markers; module-wide
  tool-input audit; `butler_session_terminations_total` telemetry counter.
