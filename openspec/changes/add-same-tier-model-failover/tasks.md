## 1. Spec Landing

- [ ] 1.1 Land this OpenSpec proposal and delta specs through the standard review flow.
- [ ] 1.2 Confirm the change does not conflict with `add-degenerate-session-guardrails`;
      guardrail terminations must remain no-retry terminal failures.
- [ ] 1.3 Confirm the change does not conflict with `redesign-settings-dispatch-console`;
      reuse its model state and failures-tail surfaces where possible.

## 2. Model Routing Candidate APIs

- [ ] 2.1 Add a resolver API in `src/butlers/core/model_routing.py` for exact-tier ordered
      candidates, applying butler overrides, enabled/state filters, priority ordering,
      and attempted-candidate exclusion.
- [ ] 2.2 Preserve existing `resolve_model()` behavior for initial selection, including
      canonical tier fallthrough.
- [ ] 2.3 Return or expose the effective tier used by initial resolution so spawner failover
      can stay within that tier.
- [ ] 2.4 Add integration tests covering same-tier next-candidate ordering, override
      priority, disabled override exclusion, state exclusion, attempted ID exclusion,
      and no cross-tier failover.

## 3. Failure Classification

- [ ] 3.1 Add a small failure classifier for spawner model failover decisions.
- [ ] 3.2 Classify pre-tool-call CLI/provider/systemic failures as failover-eligible:
      missing CLI, unregistered runtime, provider/model unavailable, auth failure,
      rate-limit before work, MCP discovery failure before tool execution, and timeout
      before tool execution.
- [ ] 3.3 Classify unknown errors, guardrail terminations, validation/business errors, and
      any failure with captured tool calls as not failover-eligible.
- [ ] 3.4 Add focused unit tests for the classifier, including default-closed behavior.

## 4. Spawner Failover Loop

- [ ] 4.1 Refactor `Spawner._run()` enough to attempt a bounded sequence of catalog
      candidates for one logical trigger without duplicating session completion.
- [ ] 4.2 Implement quota-exhaustion skip to the next same-tier candidate before adapter
      invocation; preserve the existing hard block when no candidate remains.
- [ ] 4.3 Implement runtime-failure failover when the classifier approves and no captured
      tool calls exist.
- [ ] 4.4 Suppress failover and preserve existing failure behavior whenever captured tool
      calls exist or the classifier rejects the error.
- [ ] 4.5 Ensure no catalog entry is attempted more than once for the same logical session.
- [ ] 4.6 Ensure final `SpawnerResult.model` and session row model reflect the successful
      fallback when fallback succeeds.

## 5. Provenance, Schema, And Observability

- [ ] 5.1 Choose the smallest durable provenance shape: extend `dispatch_failures` and
      session process logs if sufficient; otherwise add `public.model_dispatch_attempts`.
- [ ] 5.2 If adding a public table, add a core Alembic migration, targeted grants, and update
      RFC 0006 / `openspec/specs/database-security/spec.md` write authorization docs.
- [ ] 5.3 Expose failed primary, quota skips, fallback success, suppression reason, and
      failover exhaustion through the existing model failure-tail API or a minimal sibling
      endpoint.
- [ ] 5.4 Emit metrics for failover attempts, suppressed failovers, and exhausted failovers.
- [ ] 5.5 Add API tests for provenance visibility and migration tests for any schema changes.

## 6. Adapter Error Surface Review

- [x] 6.1 Audit Codex, Claude Code, Gemini, and OpenCode adapter exceptions and process
      metadata to ensure the classifier can distinguish systemic failures from normal
      session failures.
      — Audit complete: see `docs/reports/failover-classifier-audit-2026-05-24.md`.
      Two concrete bugs found and fixed: (a) Gemini adapter did not raise RuntimeError
      for non-zero exits (HIGH — failover dead code); (b) Codex compact_remote failures
      not covered in classifier rate-limit markers (MEDIUM — false negative).
- [x] 6.2 Add or update adapter tests for pre-tool-call CLI failure, rate limit, timeout,
      and MCP discovery failure surfaces.
      — Added tests in `tests/adapters/test_gemini_adapter.py` for non-zero exit paths
      (auth, rate-limit, network, exit-code fallback, process_info recording).
      Added tests in `tests/core/test_failover_classifier.py` for compact_remote coverage.
- [x] 6.3 Keep adapter-internal retry behavior separate from cross-model failover; record
      both forms of provenance without conflating them.
      — Verified compliant: Codex adapter's internal retry loops (transient CLI + MCP
      discovery) exhaust before propagating to spawner. last_process_info records
      retry_attempted/retry_succeeded/attempt_count independently of spawner failover
      metrics (record_failover_attempt/suppressed/exhausted).

## 7. End-To-End Verification

- [ ] 7.1 Add spawner tests for quota skip to fallback success.
- [ ] 7.2 Add spawner tests for quota exhausted with no fallback preserving hard-block error.
- [ ] 7.3 Add spawner tests for systemic runtime failure before tool calls retrying same tier.
- [ ] 7.4 Add spawner tests for runtime failure after captured tool calls suppressing retry.
- [ ] 7.5 Add spawner tests for failover exhaustion recording all attempts.
- [ ] 7.6 Run targeted tests for model routing, quota enforcement, spawner dispatch failures,
      and adapter error classification.

## 8. Documentation And Handoff

- [ ] 8.1 Update `docs/runtime/model-routing.md` and `docs/runtime/spawner.md` to describe
      same-tier failover and side-effect gating.
- [ ] 8.2 Add AGENTS.md notes for the failover safety contract if implementation reveals
      durable repository-specific lessons.
- [ ] 8.3 Create a final reconciliation/report bead that maps every requirement in this
      change to implementation and tests before syncing specs.
