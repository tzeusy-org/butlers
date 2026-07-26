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
- [ ] 4.2 Run `openspec validate --strict` on the changed specs.
- [x] 4.3 Backend: ruff lint/format, targeted pytest, one full non-e2e pytest pass.
- [x] 4.4 Frontend: eslint, full vitest, `npm run build`.

## 5. Deferred (reported as follow-ups, not implemented here)

- [ ] 5.1 Collapse the pre-spawn gate chain (permission / quota / ceiling) into the resolve CTE (rest of slice 3).
- [ ] 5.2 Speculative prewarm fired fire-and-forget from the classification decision, off the spawn critical path (slice 4).
- [ ] 5.3 Bounded routing-decision cache -- routing decisions only, never act sessions or cached answers wearing freshness (slice 5).
