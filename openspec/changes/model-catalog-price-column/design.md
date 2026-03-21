## Context

The model catalog table (`ModelCatalogCard.tsx`) currently has 9 columns: Alias, Runtime, Model ID, Extra Args, Priority, Enabled, 24h, 30d, Actions. Pricing data lives in `pricing.toml`, loaded at startup into a `PricingConfig` singleton via `init_pricing()` / `get_pricing()` in `src/butlers/api/deps.py`. The cost router (`/api/costs/`) uses this config server-side but no endpoint exposes the raw per-model price map to the frontend.

The catalog stores `model_id` values like `ollama/qwen2.5-coder:7b` and `opencode-go/minimax-m2.5`, while `pricing.toml` keys use the base model names (`ollama/qwen2.5-coder:7b`, `minimax-m2.5`). OpenCode models use `opencode-go/` prefix in the catalog but bare names in pricing.toml, so the lookup must strip the `opencode-go/` prefix.

## Goals / Non-Goals

**Goals:**
- Show per-model pricing inline in the catalog table so users see cost at a glance
- Expose pricing data via a lightweight API endpoint
- Handle missing pricing gracefully (show `–`)
- Show `Free` for zero-cost local models

**Non-Goals:**
- Editing pricing from the UI (pricing.toml remains the source of truth)
- Tiered pricing display (show the base/lowest tier only)
- Cost estimation or running-total in the catalog (that's the cost dashboard's job)

## Decisions

### 1. New endpoint: `GET /api/settings/pricing`

Returns a flat map of `{ [model_id]: { input_per_million, output_per_million } }` derived from `PricingConfig`. For tiered models, return the lowest tier (context_threshold=0).

**Why a new endpoint** rather than embedding pricing in `GET /api/settings/models`: Pricing is a separate concern (sourced from a config file, not the DB). Embedding it would require the model-settings router to depend on the pricing subsystem and would change the `ModelCatalogEntry` schema, potentially breaking other consumers. A separate lookup keeps concerns clean and is easy to cache.

**Alternative considered — inline in ModelCatalogEntry:** Rejected because it couples catalog CRUD to pricing config and requires schema migration on the response type.

### 2. Frontend lookup by model_id with prefix stripping

The frontend joins pricing data to catalog entries by `model_id`. Before lookup, strip the `opencode-go/` prefix since pricing.toml uses bare model names. Ollama models keep their `ollama/` prefix (pricing.toml keys match).

### 3. Column placement and formatting

- Position: after "Extra Args", before "Priority" (as requested)
- Format: `$0.30 / $1.20` (input / output per 1M tokens)
- Zero-cost models: show `Free` badge
- Unknown models: show `–`
- Tiered models: show base-tier price (no tooltip for now — non-goal)

### 4. Fetch strategy

Use a separate `useQuery` hook (`usePricingMap`) fetched once on mount with a long stale time (pricing rarely changes during a session). The catalog table component joins the two queries client-side.

## Risks / Trade-offs

- **[Stale pricing after toml edit]** → Pricing only reloads on server restart. Acceptable since pricing changes are infrequent and the cost dashboard has the same behavior.
- **[Model ID mismatch]** → If a new runtime prefix is added beyond `opencode-go/`, the strip logic won't match. Mitigated by documenting the convention and keeping the strip list small.
