## Why

The Model Catalog currently has no mechanism to limit token consumption per model alias. A misconfigured schedule, runaway butler, or unexpectedly verbose prompt can burn through expensive API quota (especially on `high` and `extra_high` tiers) with no guardrail. We need per-alias rolling-window token budgets with hard enforcement at spawn time and clear dashboard visibility so the operator can see consumption at a glance and intervene.

## What Changes

- **New `shared.token_usage_ledger` table** — records per-session token usage keyed by `catalog_entry_id`, enabling fast windowed aggregation without querying the sessions table.
- **New `shared.token_limits` table** — stores per-catalog-entry 24h and 30d token budgets, with independent `reset_24h_at` and `reset_30d_at` timestamps for per-window manual quota resets.
- **Pre-spawn quota enforcement** — after `resolve_model()` selects a candidate, the spawner checks the ledger against that alias's limits. If either window is exhausted, the spawn is hard-blocked with an explicit error.
- **Post-spawn ledger write** — on session completion, the spawner records `(catalog_entry_id, input_tokens, output_tokens)` to the ledger.
- **Adapter token reporting contract** — all runtime adapters MUST return `input_tokens` and `output_tokens` in their `usage` dict. Adapters that don't currently report usage will be updated.
- **Dashboard usage columns** — the `/butlers/settings` model catalog table gains two new columns (24h, 30d) showing usage-vs-limit progress bars (green→yellow→red) with a per-alias reset button.
- **API endpoints** — new routes for reading/setting token limits, querying current usage, and resetting windows per catalog entry.

## Capabilities

### New Capabilities
- `catalog-token-limits`: Rolling-window (24h and 30d) token budgets per catalog entry, with DB schema, pre-spawn enforcement, post-spawn ledger recording, manual reset, and dashboard visibility.

### Modified Capabilities
- `model-catalog`: The spawner integration gains a pre-spawn quota check and post-spawn ledger write. `resolve_model()` callers must handle a new "quota exhausted" rejection path. All runtime adapters must satisfy the token-reporting contract.

## Impact

- **Database**: Two new tables in `shared` schema (`token_usage_ledger`, `token_limits`), one new Alembic migration.
- **Spawner (`src/butlers/core/spawner.py`)**: New quota check before adapter invocation; new ledger write after session completion. Must propagate `catalog_entry_id` through the spawn flow (currently only `model` string is tracked).
- **Runtime adapters (`src/butlers/core/runtimes/`)**: Each adapter's `invoke()` must return `{"input_tokens": int, "output_tokens": int}` in its usage dict. Audit all adapters for compliance.
- **Model settings API (`src/butlers/api/routers/model_settings.py`)**: New endpoints for limits CRUD, usage queries, and reset. Existing `GET /api/settings/models` response gains usage/limit fields.
- **Dashboard frontend**: Model catalog table on `/butlers/settings` gains 24h and 30d usage columns with progress bars and reset buttons.
- **Discretion dispatcher (`src/butlers/connectors/discretion_dispatcher.py`)**: Also subject to quota enforcement — uses `resolve_model()` with `DISCRETION` tier. Must record usage to ledger.
