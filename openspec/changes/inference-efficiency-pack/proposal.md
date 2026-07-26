## Why

bu-ep4ks.13 (2026-07-25 JARVIS pursuit dossier, ranked move #13). The routing
loop is correct but latency- and cost-naive: `duration_ms` is computed for
the session ledger and audit log, then discarded before it ever reaches
`public.model_dispatch_attempts` -- so a working-but-slow model is never
demoted (cf. the 436s opencode incident) and same-priority ties fall to a
blind round-robin regardless of which candidate has actually been fast,
cheap, and reliable. Separately, the dispatch-outcome circuit breaker's
`_BREAKER_OPEN_CTE` runs a `ROW_NUMBER() OVER (PARTITION BY catalog_entry_id
...)` window across the *entire* `model_dispatch_attempts` table on every
single resolution -- a full-table scan on a fragile shared data plane that
only grows as the fleet dispatches, when the catalog it needs to check
against is a small, bounded table.

## What Changes

- **Migration `core_187`**: add a nullable `duration_ms` column to
  `public.model_dispatch_attempts`.
- **Spawner**: persist `duration_ms` on every dispatch-attempt outcome that
  actually invoked a runtime (`success`, `runtime_failure`, `suppressed`,
  `exhausted`); pre-invocation gate denials (`quota_skip`,
  `breaker_open_override`) stay `NULL` -- no invocation happened, so a
  duration would be fabricated. Also closes a related gap: a `success` row
  was previously written only when failover occurred, so the common
  single-shot success path (the 436s-slow-but-working case) left zero
  duration evidence anywhere. `success` is now recorded unconditionally.
- **Evidence-based routing score** (`butlers.core.model_routing`):
  `compute_routing_score` combines recent success rate, p95 latency, and a
  reference per-call USD cost into one score; `get_routing_evidence` /
  `get_routing_scores` batch-fetch it (id-bound, index-friendly). `resolve_model`
  / `resolve_model_with_effective_tier` now pick the best-scoring same-priority
  candidate once at least two candidates have `_EVIDENCE_MIN_SAMPLES` (5)
  qualifying attempts; below that threshold, selection falls back unchanged to
  the original round-robin counter -- a fleet with no history, or all-tied
  scores, behaves exactly as before. This is a **MODIFIED** requirement on
  the `model-catalog` capability's "Priority tie-breaking via round-robin"
  scenario.
- **Models tab**: `GET /api/settings/models` surfaces `routing_score` and a
  `routing_score_insufficient_data` flag per entry (never a fabricated score
  below the sample threshold), following the same degraded-source-honesty
  vocabulary as `breaker_open`.
- **Breaker CTE perf fix**: `_BREAKER_OPEN_CTE` rewritten from an unbounded
  window over the whole `model_dispatch_attempts` table to a correlated
  subquery per `public.model_catalog` row (bounded by the small catalog size,
  index-scan-friendly via `idx_model_dispatch_attempts_catalog_ts`). Same
  trigger condition, same result set -- verified against the full existing
  breaker test suite plus new isolation coverage.

## Deferred (reported as follow-ups, not implemented here)

Folding the second breaker probe, token-quota check, and monthly-ceiling
check into a single index-bound resolve round-trip (the rest of slice 3) was
judged too invasive to land safely alongside the above in one PR -- it would
restructure the pre-spawn gate chain's control flow on the safety-critical
dispatch path. Filed as a follow-up bead. Speculative prewarm off the
critical path (slice 4) and a bounded routing-decision cache (slice 5) are
likewise deferred to follow-up beads; see the worker report for exact scope
cuts.

## Impact

- Affected specs: `model-catalog` (MODIFIED: Priority tie-breaking scenario).
- Affected code: `alembic/versions/core/core_187_dispatch_attempt_duration.py`,
  `src/butlers/core/spawner.py`, `src/butlers/core/model_routing.py`,
  `src/butlers/api/routers/model_settings.py`,
  `frontend/src/pages/SettingsModelsPage.tsx`, `frontend/src/api/types.ts`.
