## 1. Spec Landing

- [ ] 1.1 Land this proposal and its delta specs (`core-spawner`, `core-sessions`, `core-modules`, `module-memory`, `runtime-opencode`) via the standard OpenSpec review workflow.
- [ ] 1.2 Confirm with the parallel worker owning the `_merge_tool_call_records` fix that the tool-name merge scope stays out of this change.

## 2. Runtime Config Plumbing

- [ ] 2.1 Add three new fields to `RuntimeSeedConfig` in `src/butlers/config.py`: `degenerate_loop_max_repeats: int = 5`, `max_tool_calls_per_session: int = 80`, `max_input_tokens_per_session: int = 500_000`.
- [ ] 2.2 Add matching columns to `{schema}.runtime_config` via a new Alembic migration; wire `RuntimeConfigAccessor.get()` to surface them. Classify all three as HOT fields (read per-spawn).
- [ ] 2.3 Update `butler.toml` parser tests to cover the new fields and to accept their absence (defaults apply).
- [ ] 2.4 Document the new knobs in the butler runtime-config runbook / `AGENTS.md`.

## 3. Spawner Guardrails

- [ ] 3.1 Implement a `DegenerateLoopDetector` in `src/butlers/core/spawner.py` (or a sibling module). It tracks consecutive identical `(tool_name, canonical_json(args))` tuples and trips at `degenerate_loop_max_repeats`.
- [ ] 3.2 Implement cumulative tool-call count and cumulative input-token tracking in the spawner's tool-call / usage event consumer.
- [ ] 3.3 On any budget trip: cancel the `runtime.invoke()` task so the subprocess is terminated, and record a typed `error` string (`degenerate_tool_loop`, `tool_call_budget_exceeded`, `token_budget_exceeded`) via `session_complete(success=False, error=...)`.
- [ ] 3.4 Ensure the self-healing dispatcher still fires for these terminations (they are real failures).
- [ ] 3.5 Unit tests: simulate an adapter stream that loops, one that exceeds the tool-call cap, one that exceeds the token cap, and one that behaves normally. Verify the correct `error` string on each.
- [ ] 3.6 Integration test: run a real adapter (preferably opencode, the offender) through a synthetic loop prompt and confirm the session ends with `degenerate_tool_loop`.

## 4. Adapter Event Stream Contract

- [ ] 4.1 Confirm all four adapters (`claude`, `codex`, `gemini`, `opencode`) can surface tool-call events and per-event usage increments to the spawner incrementally. Update any adapter that buffers until end-of-session.
- [ ] 4.2 If any adapter cannot stream incremental events, document the limitation and fall back to end-of-session enforcement for that adapter only (with a warning log).

## 5. Sessions Table / Error Taxonomy

- [ ] 5.1 Verify the `sessions.error` column has sufficient length for the longest new value (`tool_call_budget_exceeded`, 26 chars). If the column is bounded to <32, extend it; otherwise no migration is needed.
- [ ] 5.2 Ensure the taxonomy values are documented in one place (a shared constants module) and referenced by both the spawner and the dashboard.

## 6. Module Tool-Input Audit (follow-up)

- [ ] 6.1 Open a tracking bead (or equivalent) to audit every module's MCP tool surface and ensure each raises on invalid input rather than returning an empty success payload. **Do not perform the audit in this change** — just file the task.
- [ ] 6.2 The audit must cover at minimum: `module-memory`, `module-calendar`, `module-email`, `module-telegram`, `module-contacts`, `module-approvals`, and every other module with an `MCP tool` registration. Each finding becomes its own PR.

## 7. Memory Entity Resolve

- [ ] 7.1 Coordinate with the parallel worker fixing `memory_entity_resolve` in code so the spec update and the code change land together.
- [ ] 7.2 Confirm the raise-on-null behavior is covered by a unit test: `await memory_entity_resolve(name=None)` MUST raise `ValueError` (or the project's standard invalid-input exception) and MUST NOT return `[]`.

## 8. OpenCode Timeout Verification

- [ ] 8.1 Reproduce the 436 s over-timeout behavior against session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a`. Identify whether the timeout was never reaching the subprocess, or whether the subprocess ignored SIGTERM and required SIGKILL.
- [ ] 8.2 If SIGTERM was ignored: update `OpenCodeAdapter` to escalate to SIGKILL after a short grace period (e.g. 5 s) and add a test covering the escalation.
- [ ] 8.3 If the timeout was not being plumbed: fix the plumbing. Add a spawner-level integration test that asserts the subprocess is dead within `timeout + grace`.
- [ ] 8.4 Update `runtime-opencode` spec to document the grace-and-kill behavior (already drafted in this change's delta spec).
- [ ] 8.5 Add the narrow OpenCode SQLite migration bootstrap retry path and regression coverage for success, partial/error output, stdout-bearing failures, and retry-failure provenance.

## 9. Dashboard Surfacing

- [ ] 9.1 Add human-readable labels for the three new `error` values in the sessions UI (detail view and list filter).
- [ ] 9.2 Add a filter in the sessions list so operators can query "degenerate loop" sessions.
- [ ] 9.3 Add a Grafana panel driven by the new `butler_session_terminations_total{butler,reason}` counter.

## 10. Telemetry

- [ ] 10.1 Emit `butler_session_terminations_total{butler, reason}` from `session_complete`. Increment on every session close, not just the degenerate ones.
- [ ] 10.2 Add the metric to the standard scrape path; confirm Prometheus is collecting it.

## 11. Documentation

- [ ] 11.1 Update the butler-author runbook with: what the three new termination reasons mean, how to triage, how to tune thresholds per butler.
- [ ] 11.2 Update `about/craft-and-care` (or equivalent) with the "tools raise on invalid input" rule so future modules comply by construction.
- [ ] 11.3 Add a short postmortem entry referencing session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a` so the motivating incident is not lost to history.
