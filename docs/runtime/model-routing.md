# Model Routing

> **Purpose:** Describe the dynamic model selection system: the catalog, per-butler overrides, complexity tiers, token quotas, and usage tracking.
> **Audience:** Developers configuring model selection, operators managing token budgets, architects understanding the cost optimization strategy.
> **Prerequisites:** [LLM CLI Spawner](spawner.md), [Session Lifecycle](session-lifecycle.md).

## Overview

Model routing (`src/butlers/core/model_routing.py`) selects the best AI model for each spawner invocation based on task complexity and butler identity. Rather than hardcoding a single model per butler, the system uses a shared catalog with per-butler overrides and complexity tiers. This enables cost optimization --- cheap models for simple tasks, capable models for complex ones --- and allows operators to tune model selection without changing butler code.

## Complexity Tiers

The `Complexity` enum defines six tiers that drive model selection:

| Tier | Value | Typical Use |
| --- | --- | --- |
| `TRIVIAL` | `trivial` | Simple lookups, status checks, quick responses |
| `MEDIUM` | `medium` | Standard tasks (default for most triggers) |
| `HIGH` | `high` | Complex reasoning, multi-step analysis |
| `EXTRA_HIGH` | `extra_high` | Very complex tasks requiring top-tier models |
| `DISCRETION` | `discretion` | Model selection delegated to the catalog's priority ordering |
| `SELF_HEALING` | `self_healing` | Reserved for self-healing dispatch |

## The Model Catalog

The `shared.model_catalog` table is the global registry of available models. Each entry has:

- **`id`** --- UUID primary key (referenced by quota and usage tables)
- **`runtime_type`** --- the adapter to use (`"claude"`, `"codex"`, `"gemini"`, `"opencode"`)
- **`model_id`** --- the model identifier string
- **`complexity_tier`** --- which complexity tier this entry serves
- **`priority`** --- numeric priority (higher wins when multiple entries match)
- **`enabled`** --- whether this entry is active
- **`extra_args`** --- JSONB list of CLI token strings passed to the adapter
- **`created_at`** --- tie-breaker for entries with equal priority (older entries win)

## Per-Butler Overrides

The `shared.butler_model_overrides` table allows per-butler customization without duplicating catalog entries. An override row references a catalog entry and can remap `enabled`, `priority`, and `complexity_tier`. Overrides use `COALESCE` semantics: when an override field is NULL, the catalog value is used.

## Resolution Algorithm

`resolve_model(pool, butler_name, complexity_tier)` executes a single SQL query:

1. LEFT JOIN `shared.model_catalog` with `shared.butler_model_overrides` on the butler name and catalog entry ID.
2. Compute effective values via COALESCE for enabled, priority, and complexity_tier.
3. Filter: effective `enabled = true` AND effective `complexity_tier = $tier`.
4. Order by effective `priority DESC`, then `created_at ASC` (stable tie-break).
5. Return the first matching row as `(runtime_type, model_id, extra_args, catalog_entry_id)`, or `None`.

When `resolve_model()` returns `None`, the spawner falls back to the model configured in `[butler.runtime].model` in `butler.toml`.

## Token Quotas

The quota system prevents runaway costs by limiting token consumption per model on rolling time windows.

### Quota Check

`check_token_quota(pool, catalog_entry_id)` returns a `QuotaStatus` dataclass with `allowed`, `usage_24h`, `limit_24h`, `usage_30d`, and `limit_30d`. The check uses a CTE-based single round-trip query. A fast path skips the ledger query when no limits row exists. The check is **fail-open**: database errors return `allowed=True`. The quota guardrail must never block all sessions.

### Token Usage Recording

`record_token_usage()` writes to `shared.token_usage_ledger` after each session completes. This is best-effort: errors are logged and never propagate to the caller.

## Resolution Flow in the Spawner

1. Call `resolve_model(pool, butler_name, complexity)` to query the catalog.
2. If found, set `resolution_source = "catalog"`. If not, fall back to TOML model with `resolution_source = "toml_fallback"`.
3. Call `check_token_quota()` for catalog-resolved models.
4. If quota returns `allowed=False`, skip the session with a warning.
5. After completion, call `record_token_usage()` to update the ledger.

Both `resolution_source` and `complexity` are recorded on the session row for observability.

## Related Pages

- [LLM CLI Spawner](spawner.md) --- where model resolution integrates into the spawn pipeline
- [Session Lifecycle](session-lifecycle.md) --- how resolution metadata is recorded on sessions
- [Scheduler Execution](scheduler-execution.md) --- how scheduled tasks specify complexity tiers
