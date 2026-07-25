## Why

`infra_conditions` (bu-27dxl.6.2) proved durable open/aging/auto-resolve/
re-escalate semantics for infrastructure reliability evidence, but its
docstring scoped it to infrastructure (deploy drift, calendar sync deadman).
Owner-facing standing concerns (an overdue bill, a spending anomaly still
true this month, eventually a refill due or an expiring document) exist only
as re-fired `insight_candidates` suppressed by `cooldown_days` — a producer
re-proposes the same candidate on a fixed cadence regardless of whether the
underlying concern is still true, which structurally cannot express "still
true and still unactioned" (there is no durable state to query, only a
delivery-dedup timer). This converts proactivity from edge-and-forget to
level-triggered attention using the exact lifecycle machinery the fleet
already hardened this cycle, without touching `infra_conditions` itself.

## What Changes

- Extract `infra_conditions`' reconciliation engine (fingerprinting, open/
  confirm/escalate/resolve, the advisory-lock concurrency contract, reads)
  into a shared, table-parametrized module (`butlers.core.condition_ledger`).
  `infra_conditions.py` becomes a thin facade over it, unchanged in every
  public signature and behavior.
- Add `public.owner_conditions` (core_184), a table identical in shape to
  `infra_conditions`, and `butlers.core.owner_conditions` as its own thin
  facade over the same engine.
- Add a Switchboard MCP tool `reconcile_owner_condition` so an LLM-driven
  butler session (no raw DB pool) can reconcile a standing concern while
  staying MCP-only. A deterministic scheduled job with a raw pool calls
  `owner_conditions.reconcile_snapshot` directly and in-process instead — the
  same split `propose_insight_candidate` already has.
- Migrate two Finance butler scheduled-job categories (overdue bills,
  spending anomalies) to also reconcile into the owner condition ledger
  alongside their existing cooldown-gated insight-candidate submission (a
  state side effect, not a replacement of the delivery path).
- Extend `GET /api/system/conditions` with an optional `ledger` query param
  (`infra` default, or `owner`) and extend the dashboard's existing Standing
  Conditions panel to fetch and merge both ledgers into one list, tagged per
  row, rather than shipping a duplicate panel.
- Calendar overcommitment radar and ingestion-time threshold watchers
  (HA sensors, health vitals, spend running total) are out of scope for this
  change; `owner_conditions.reconcile_snapshot` is a stable, documented
  extension point for them to adopt without further schema changes.

## Capabilities

### New Capabilities

- `owner-condition-ledger`: the durable, level-triggered lifecycle for
  owner-facing standing concerns, generalized from `infrastructure-
  reliability`'s representation.

### Modified Capabilities

- `system-overview-page`: `GET /api/system/conditions` gains a `ledger`
  selector, and the Standing Conditions panel renders both ledgers merged.
- `proactive-insight-engine`: two Finance scheduled-job categories also
  reconcile into the owner condition ledger, a state side effect alongside
  (not a replacement of) candidate submission.

## Impact

- `src/butlers/core/condition_ledger.py` (new), `src/butlers/core/
  infra_conditions.py` (refactored facade), `src/butlers/core/
  owner_conditions.py` (new facade)
- `alembic/versions/core/core_184_owner_conditions.py`
- `roster/switchboard/modules/owner_conditions_broker.py`,
  `roster/switchboard/modules/__init__.py`, `roster/switchboard/butler.toml`
- `roster/finance/jobs/finance_jobs.py`
- `src/butlers/api/routers/system.py`
- `frontend/src/api/{client,types}.ts`, `frontend/src/components/system/
  StandingConditionsTile.tsx`
- Tests: `tests/core/test_owner_conditions.py`, `tests/integration/
  test_owner_conditions_roundtrip.py`, `tests/migrations/
  test_owner_conditions_migration.py`, `tests/modules/
  test_module_owner_conditions_broker.py`, `roster/finance/tests/
  test_jobs.py`, `tests/api/test_system.py`, `frontend/src/components/
  system/StandingConditionsTile.test.tsx`

No changes to `infra_conditions`' stored data, its producers (`deploy_drift`,
`calendar_sync_deadman`), or any existing `GET /api/system/conditions`
caller that omits `ledger` (defaults to `infra`, byte-identical response
shape plus one new `ledger` field).
