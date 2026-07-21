## 1. Pricing truthfulness

- [x] 1.1 Add failing unit coverage proving an unknown model price remains `None` while an explicitly classified zero-marginal model remains `0.0`.
- [x] 1.2 Add validated billing-class support and explicit gpt-5.6 subscription pricing entries.
- [x] 1.3 Return a structured priced-subtotal and unpriced-model envelope from the shared ledger pricing path, and retain it in the monthly-ceiling status.

## 2. Ledger-authoritative Spend API

- [x] 2.1 Add failing API tests for executed-model ledger aggregation across summary, daily, model/butler/purpose breakdowns, and forecast.
- [x] 2.2 Add range-bounded ledger GROUP BY helpers and repoint the named Spend dollars and token actuals away from `sessions.model` pricing.
- [x] 2.3 Propagate `unpriced_models`, billing classes, ceiling blindness, and degraded ledger evidence through API models and endpoint responses.
- [x] 2.4 Add the sessions-versus-ledger divergence detector and historical requested-model attribution note without using session data for dollars.

## 3. Spend dashboard truthfulness

- [x] 3.1 Add failing frontend coverage for `—/unpriced`, known subscription zero cost, ceiling blindness, divergence, and historical-attribution notices.
- [x] 3.2 Update Spend API TypeScript types and page rendering to present the structured truthfulness/deadman data without zero-value fallbacks.

## 4. Verification and handoff

- [x] 4.1 Run targeted pricing, routing, Spend API, and Spend page tests; fix all failures.
- [x] 4.2 Validate the OpenSpec change strictly and run the required lint/type/build quality gates for changed Python and frontend surfaces.
- [x] 4.3 Rebase on fresh `origin/main`, re-run focused verification, commit scoped changes, push the worker branch, and open a PR.

## Recovery verification (2026-07-22)

- Rebased the correction branch on `origin/main@8397e72585d37091722aabd7650e895d9123e73f`; `git range-diff` maps both original PR commits exactly.
- [x] `uv run pytest tests/api/test_pricing.py tests/core/test_model_routing_quota.py tests/api/test_spend.py tests/core/test_core_spawner.py::TestSpendEventBusWiring -q --tb=short` — 127 passed.
- [x] `npm test -- --run src/hooks/use-spend-ticker.test.ts src/pages/SpendPage.test.tsx src/components/costs/CostWidget.test.tsx` — 57 passed.
- [x] `npm run lint:emdash`, `npm run lint` (exit 0; one existing unrelated HealthOverviewPage warning), `npm run build`, scoped Ruff, and `openspec validate spend-ledger-truth --strict` completed successfully.
