## 1. Backend — Pricing Lookup Endpoint

- [x] 1.1 Add `GET /api/settings/pricing` route in `src/butlers/api/routers/model_settings.py` that returns the pricing map from `PricingConfig` (flat dict of `model_id → { input_per_million, output_per_million }`; tiered models use lowest tier)
- [x] 1.2 Add Pydantic response model for the pricing map
- [x] 1.3 Add tests for the pricing endpoint (known model, unknown model, zero-cost model, tiered model returns base tier)

## 2. Frontend — Pricing Hook and Types

- [x] 2.1 Add `PricingMap` type to `frontend/src/api/types.ts`
- [x] 2.2 Add `usePricingMap()` hook in `frontend/src/hooks/use-model-catalog.ts` that fetches `GET /api/settings/pricing` with a long stale time
- [x] 2.3 Add helper function to resolve a catalog `model_id` to a pricing key (strip `opencode-go/` prefix)

## 3. Frontend — Price Column in Model Catalog Table

- [x] 3.1 Add "Price" column header after "Extra Args" in `ModelCatalogCard.tsx`
- [x] 3.2 Render price cell: `$X.XX / $Y.YY` for known pricing, `Free` for zero-cost, `–` for unknown
- [x] 3.3 Update skeleton loader to account for the additional column

## 4. Verification

- [x] 4.1 Run backend tests (`pytest tests/api/test_pricing.py tests/api/test_model_settings*.py`)
- [x] 4.2 Run frontend lint/build
- [ ] 4.3 Visual check on the Settings page — verify column renders for Claude, OpenCode, Ollama, and unknown models
