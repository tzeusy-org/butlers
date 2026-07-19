## Why

The Spend dashboard currently converts an absent price into `$0.00` and prices
several daily aggregates from `sessions.model`, which records the requested
model rather than the catalog entry that actually consumed tokens. That creates
false zero-cost signals for the most-used gpt-5.6 models and can attribute
historical spend to models that never ran, violating the project's
"nothing fabricated" doctrine.

## What Changes

- Add explicit billing classifications for gpt-5.6 catalog pricing and preserve
  an unknown price as unpriced rather than silently treating it as zero.
- Make the ledger-backed cost calculation return both the priced subtotal and
  an `unpriced_models` envelope; propagate that envelope to spend summaries,
  breakdowns, forecasts, and the monthly-ceiling visibility surface.
- Use `public.token_usage_ledger` joined to the executed catalog entry as the
  authoritative source for daily, butler, model, purpose, summary, breakdown,
  and forecast dollar figures.
- Surface a sessions-versus-ledger token divergence warning and label windows
  before 2026-07-10 whose session-model labels reflect requested rather than
  executed models.
- Render unpriced entries as `—/unpriced`, and explicitly state when the
  monthly ceiling can only see the priced portion of spend.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `dashboard-spend-dashboard`: Spend responses and the Spend page must expose
  ledger-authoritative costs, unpriced-model truthfulness, divergence evidence,
  and historical-attribution labeling.
- `catalog-token-limits`: The ledger-backed monthly ceiling must preserve and
  expose the distinction between known zero cost and unknown pricing.

## Impact

- `src/butlers/api/pricing.py` and `pricing.toml` for explicit billing classes
  and `None` for absent pricing.
- `src/butlers/core/model_routing.py` and the spend API router for structured
  ledger pricing and ceiling/deadman data.
- Spend API models, frontend types, page rendering, and targeted backend and
  frontend tests.
- No schema migration or historical data rewrite is planned: the existing
  ledger already contains the executed catalog-entry linkage needed for this
  change.
