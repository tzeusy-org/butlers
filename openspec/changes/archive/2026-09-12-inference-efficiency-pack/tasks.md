## 1. Schema and provenance

- [x] 1.1 `core_187`: nullable `duration_ms` on `public.model_dispatch_attempts`.
- [x] 1.2 Spawner: per-attempt timing wired to every outcome that invoked a runtime; `success` written unconditionally (not only on failover).

## 2. Evidence-based routing score

- [x] 2.1 `RoutingEvidence` / `RoutingScore` / `compute_routing_score` (pure function, insufficient-data gated).
- [x] 2.2 `get_routing_evidence` / `get_routing_scores` batch fetchers (id-bound, index-friendly).
- [x] 2.3 `_RESOLVE_SQL` returns all tied top-priority candidates with evidence instead of picking a winner in SQL; `_select_resolved_row` picks by score when sufficient evidence exists, else legacy round-robin.
- [x] 2.4 Models tab: `routing_score` + `routing_score_insufficient_data` surfaced on `GET /api/settings/models`; frontend badge with tooltip breakdown.

## 3. Breaker CTE perf fix

- [x] 3.1 `_BREAKER_OPEN_CTE` rewritten to a catalog-bounded correlated subquery; existing breaker test suite passes unchanged.

## 4. Contract and verification

- [x] 4.1 `model-catalog` spec delta (MODIFIED: Priority tie-breaking scenario).
- [x] 4.2 Run `openspec validate --strict` on the changed specs.
- [x] 4.3 Backend: ruff lint/format, targeted pytest, one full non-e2e pytest pass.
- [x] 4.4 Frontend: eslint, full vitest, `npm run build`.

## 5. Rest of slice 3 + slices 4-5 (bu-k9te9, follow-up to bu-ep4ks.13 / PR #3587)

- [x] 5.1 Fold the quota/ceiling pre-spawn gates into the resolve CTE (rest of slice 3).
      Token-quota gate: `resolve_model_with_effective_tier(quota_aware=True)` folds a
      per-candidate `quota_ok` column into `_RESOLVE_SQL`; the spawner's sequential
      `check_token_quota`/`next_same_tier_candidate` loop is skipped whenever the fold
      proves the top-priority band has quota headroom (raises `TierQuotaExhausted`
      otherwise, falling back to the unchanged sequential loop). Ceiling gate:
      `check_monthly_ceiling` is kicked off concurrently (`asyncio.create_task`) with the
      permission/quota gates instead of sequentially after them; its DENY decision still
      fires in the same position. Permission is deliberately NOT folded — it is a
      per-butler authorization check with no per-candidate meaning, so it stays exactly
      where it was.
- [x] 5.2 Speculative prewarm fired fire-and-forget from the classification decision, off the spawn critical path (slice 4).
      `Spawner._fire_speculative_prewarm` fires as soon as the resolved runtime_type
      settles (post spend-rule override): MCP endpoint warmup + a new
      `RuntimeAdapter.speculative_prewarm()` hook (`CodexAdapter` override reuses the
      existing token-refresh pre-warm, idempotent via `_prewarm_done`). Never awaited by
      the dispatch path; every operation swallows its own exceptions; writes no
      `model_dispatch_attempts` provenance.
- [x] 5.3 Bounded routing-decision cache -- routing decisions only, never act sessions or cached answers wearing freshness (slice 5).
      `_fetch_resolve_rows` serves `_RESOLVE_SQL`'s candidate rows from a short-TTL
      (5s), size-bounded (256 entries, LRU) in-process cache for quota-unaware calls
      only; the round-robin counter increment was split into a standalone query so it
      still fires every call regardless of cache hit/miss. Never applied to the
      quota-aware path, `next_same_tier_candidate`, or empty results (an empty answer is
      exactly the state most likely to change soon). TTL-based rather than event-driven
      invalidation — justified in `_fetch_resolve_rows`'s docstring.
- [x] 5.4 Discretion quota skips (bu-x82cy): reconcile `model-catalog` and
      `catalog-token-limits` so token limits are per-catalog-entry availability caps for
      discretion dispatch, then make a denied candidate a bounded, pre-invocation,
      same-effective-tier skip. Skips consume the existing attempt cap; terminal
      quota/runtime exhaustion is `same_tier_failover_exhausted`; do not import unrelated
      Spawner permission, monthly-ceiling, per-call, dashboard, or session-provenance gates.
