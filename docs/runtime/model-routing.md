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

The `public.model_catalog` table is the global registry of available models. Each entry has:

- **`id`** --- UUID primary key (referenced by quota and usage tables)
- **`runtime_type`** --- the adapter to use (`"claude"`, `"codex"`, `"gemini"`, `"opencode"`)
- **`model_id`** --- the model identifier string
- **`complexity_tier`** --- which complexity tier this entry serves
- **`priority`** --- numeric priority (higher wins when multiple entries match)
- **`enabled`** --- whether this entry is active
- **`extra_args`** --- JSONB list of CLI token strings passed to the adapter
- **`created_at`** --- tie-breaker for entries with equal priority (older entries win)

## Per-Butler Overrides

The `public.butler_model_overrides` table allows per-butler customization without duplicating catalog entries. An override row references a catalog entry and can remap `enabled`, `priority`, and `complexity_tier`. Overrides use `COALESCE` semantics: when an override field is NULL, the catalog value is used.

## Resolution Algorithm

`resolve_model(pool, butler_name, complexity_tier)` executes a single SQL query:

1. LEFT JOIN `public.model_catalog` with `public.butler_model_overrides` on the butler name and catalog entry ID.
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

`record_token_usage()` writes to `public.token_usage_ledger` after each session completes. This is best-effort: errors are logged and never propagate to the caller.

## Resolution Flow in the Spawner

`resolve_model_with_effective_tier(pool, butler_name, complexity)` is the catalog entry point used by the spawner. It returns a 6-tuple:

```
(runtime_type, model_id, extra_args, catalog_entry_id, timeout_s, effective_tier)
```

The `effective_tier` is pinned at initial resolution and used to scope all same-tier failover candidates for the logical session.

1. Call `resolve_model_with_effective_tier(pool, butler_name, complexity)` to query the catalog.
2. If found, set `resolution_source = "catalog"`. If not, fall back to TOML model with `resolution_source = "static_fallback"`.
3. Call `check_token_quota()` for catalog-resolved models (see quota section above).
4. If quota returns `allowed=False`, record a `quota_skip` row in `public.model_dispatch_attempts` and seek the next same-tier candidate via `next_same_tier_candidate()`.
5. Invoke the selected adapter.
6. After completion, call `record_token_usage()` to update the ledger.

Both `resolution_source` and `complexity` are recorded on the session row for observability.

## Same-Tier Failover

When a catalog-resolved model fails before any side effects occur, the spawner may retry using another model from the same effective tier. The **effective tier** is the tier that produced the initial candidate and is pinned for the logical session — failover never crosses tier boundaries.

### What Makes a Model Eligible for Same-Tier Failover

The `next_same_tier_candidate()` function returns the next enabled catalog entry that meets all of these conditions:

1. **Same effective tier** — matches the tier string pinned from initial resolution.
2. **Enabled** — `effective_enabled = true` after applying per-butler overrides.
3. **Not already attempted** — the catalog entry UUID is not in the `_attempted_ids` list.
4. **Priority ordering** — sorted by effective priority descending, then `created_at ASC` (stable tie-break).

Butler-level overrides (`public.butler_model_overrides`) are applied via COALESCE: when an override field is NULL, the catalog value is used.

### Quota-Skip Loop

Before invoking any adapter, the spawner checks `check_token_quota()` for the current candidate. If quota is exhausted:

1. A `quota_skip` row is written to `public.model_dispatch_attempts` with `outcome='quota_skip'`.
2. The skipped catalog entry ID is appended to `_attempted_ids`.
3. `next_same_tier_candidate()` is called with `_attempted_ids` to get the next eligible candidate.
4. If no candidate remains, `record_failover_exhausted(tier=...)` is emitted and the session fails.

All `quota_skip` rows share the same `logical_session_id` as subsequent attempt rows, enabling end-to-end provenance correlation even when the initial `request_id` is None (scheduler/tick triggers).

### Adapter Signals (bu-ojiij.5)

Each runtime adapter exposes adapter-level signals in `last_process_info` that inform the failover classifier:

- **`is_pre_tool_call`** (`bool`) — `True` when the failure happened before any MCP tool was executed. Set by all adapters on non-zero exit, timeout, and certain pre-invocation errors.
- **`error_detail`** (`str`) — Adapter-extracted error detail (stderr, structured error, etc.) for classifier pattern matching.
- **`internal_retry_count`** (`int`, on `MCPToolDiscoveryError`) — Number of adapter-internal retry attempts. The spawner treats `MCPToolDiscoveryError` as **one** logical failover attempt regardless of this count.

### Failover Classification

The `classify_failover_eligibility()` function (in `failover_classifier.py`) decides whether a failed attempt may be retried:

**Eligible (default-open for these classes):**
- Missing CLI binary (`FileNotFoundError`)
- Timeout before any tool call (`TimeoutError` with no captured calls)
- Rate-limit / auth / model-unavailable / provider-unavailable (`RuntimeError` matching known markers)
- MCP discovery failure with no captured tool calls (`MCPToolDiscoveryError`)

**Suppressed (default-closed):**
- Any captured MCP tool call — world may have been touched
- Guardrail terminations (`degenerate_tool_loop`, `tool_call_budget_exceeded`, `token_budget_exceeded`)
- Unknown error classes — cannot confirm no side effect occurred
- Business / validation errors (`ValueError`, `TypeError`)

The classifier is **default-closed**: unknown failures suppress failover to protect against duplicate side effects on retry.

### Attempt Provenance

Every attempt in the failover sequence writes a row to `public.model_dispatch_attempts`:

| `outcome` | Meaning |
|---|---|
| `quota_skip` | Candidate skipped before invocation due to quota exhaustion |
| `runtime_failure` | Adapter raised a failover-eligible error |
| `suppressed` | Failover decision was ineligible (side effects or unknown error) |
| `exhausted` | All same-tier candidates tried, none succeeded |
| `success` | This attempt produced the final successful result (only written on failover) |

Query provenance via the API: `GET /api/dispatch/attempts?session_id=<uuid>` or directly from `public.model_dispatch_attempts`.

### Metrics

Three counters track failover at the process level:

| Metric | Labels | Meaning |
|---|---|---|
| `butlers.spawner.failover_attempts_total` | `butler, from_model, to_model, reason` | Successful failover transition (primary failed, next candidate invoked) |
| `butlers.spawner.failover_suppressed_total` | `butler, reason` | Failover suppressed by classifier |
| `butlers.spawner.failover_exhausted_total` | `butler, tier` | All same-tier candidates exhausted |

## Verification

To confirm the model routing behavior described here matches the running system:

```bash
# 1. Catalog entries exist and are enabled
psql -h localhost -U butlers -d butlers -c \
  "SELECT runtime_type, model_id, complexity_tier, priority, enabled
   FROM public.model_catalog ORDER BY priority DESC, created_at;"
# Expected: at least one enabled entry per complexity tier you use

# 2. Resolution source recorded on sessions
psql -h localhost -U butlers -d butlers -c \
  "SELECT model, complexity, resolution_source, COUNT(*) as sessions
   FROM general.sessions WHERE completed_at IS NOT NULL
   GROUP BY model, complexity, resolution_source ORDER BY sessions DESC LIMIT 10;"
# Expected: resolution_source is "catalog" for catalog-resolved sessions,
#           "toml_fallback" when no catalog entry matched the tier

# 3. Token quota ledger records usage
psql -h localhost -U butlers -d butlers -c \
  "SELECT catalog_entry_id, SUM(input_tokens + output_tokens) AS total_tokens,
          MAX(recorded_at) AS last_record
   FROM public.token_usage_ledger
   WHERE recorded_at > now() - interval '24 hours'
   GROUP BY catalog_entry_id;"
# Expected: rows with non-zero totals for models used today

# 4. Failover attempts are tracked (if any occurred)
psql -h localhost -U butlers -d butlers -c \
  "SELECT outcome, failure_reason, error_code, attempt_index
   FROM public.model_dispatch_attempts ORDER BY created_at DESC LIMIT 10;"
# Expected: "success" rows for normal sessions; "quota_skip" or "runtime_failure" for failovers

# 5. Per-butler overrides apply (if configured)
psql -h localhost -U butlers -d butlers -c \
  "SELECT butler, catalog_entry_id, enabled, priority, complexity_tier
   FROM public.butler_model_overrides;"
# Expected: override rows for any butler with custom model settings;
#           NULL columns fall back to catalog defaults via COALESCE
```

## Related Pages

- [LLM CLI Spawner](spawner.md) --- where model resolution integrates into the spawn pipeline, including same-tier failover flow
- [Session Lifecycle](session-lifecycle.md) --- how resolution metadata is recorded on sessions
- [Scheduler Execution](scheduler-execution.md) --- how scheduled tasks specify complexity tiers
