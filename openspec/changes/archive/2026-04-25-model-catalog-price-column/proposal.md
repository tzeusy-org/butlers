## Why

The model catalog table on the Settings page shows runtime, model ID, args, priority, and usage — but not how much each model costs. Users must cross-reference `pricing.toml` or the cost dashboard to understand per-token rates. Adding an inline price column makes cost-awareness immediate when choosing or reordering models.

## What Changes

- Add a "Price" column to the model catalog table, positioned after "Extra Args" and before "Priority"
- Display input/output prices formatted as `$X.XX / $Y.YY` per 1M tokens
- Show `–` for models without a pricing entry (e.g. unknown or not-yet-added models)
- Show `Free` for models where both input and output prices are zero (Ollama local models)
- Expose per-model pricing from `pricing.toml` via a new API endpoint so the frontend can look up prices by `model_id`

## Capabilities

### New Capabilities

_None — this is a UI enhancement backed by existing pricing data._

### Modified Capabilities

- `dashboard-model-settings`: Add a Price column to the model catalog table UI and a pricing lookup API endpoint

## Impact

- **Frontend:** `ModelCatalogCard.tsx` — new table column + data fetch from pricing endpoint
- **Backend API:** New `GET /api/settings/pricing` endpoint returning the pricing map
- **Types:** Frontend `ModelCatalogEntry` or a separate pricing lookup type
- **No database changes** — pricing data comes from `pricing.toml` (already loaded at startup)
