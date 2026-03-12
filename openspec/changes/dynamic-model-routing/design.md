## Context

Today every butler has a single model hardcoded in `butler.toml` under `[butler.runtime].model` (currently `gpt-5.1` across the fleet). The spawner reads this once and passes it through to the `RuntimeAdapter.invoke()` call. There is no mechanism to vary the model based on task characteristics, and no way to change model assignments without editing TOML files and restarting daemons.

The system already supports four runtime adapters (claude, codex, gemini, opencode) and the spawner's `invoke()` signature already accepts `model` and `runtime_args` as parameters — so the plumbing for multi-model invocation exists. What's missing is the **decision layer**: who picks the model, based on what, and how is that configuration managed.

## Goals / Non-Goals

**Goals:**
- Dynamic model selection at spawn time based on task complexity
- A shared, DB-backed model catalog with named aliases (e.g. `gpt-5.4-high` = `gpt-5.4` + reasoning args)
- Global complexity-to-model mappings with per-butler overrides
- Switchboard classifies complexity for all trigger paths (external, scheduler, tick)
- Dashboard UI for full CRUD on model catalog, aliases, and per-butler overrides
- Seed sensible defaults on first migration (known models + common alias patterns)

**Non-Goals:**
- Auto-scaling or cost budgeting (future work)
- Dynamic complexity re-classification mid-session (model is chosen once at spawn)
- Automatic model discovery or vendor API probing
- Changing the RuntimeAdapter interface itself

## Decisions

### 1. Schema: Two tables in `shared`

**`shared.model_catalog`** — The canonical registry of available model configurations.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` PK | |
| `alias` | `text` UNIQUE | Human-readable name, e.g. `claude-opus-4`, `gpt-5.4-high` |
| `runtime_type` | `text` NOT NULL | Maps to adapter registry: `claude`, `codex`, `gemini`, `opencode` |
| `model_id` | `text` NOT NULL | Actual model string passed to adapter, e.g. `claude-opus-4-0-20250514` |
| `extra_args` | `jsonb` DEFAULT `[]` | Additional CLI args, e.g. `["--config", "model_reasoning_effort=high"]` |
| `complexity_tier` | `text` NOT NULL | One of `trivial`, `medium`, `high`, `extra_high` |
| `enabled` | `boolean` DEFAULT `true` | Global kill switch per entry |
| `priority` | `int` DEFAULT `0` | Tie-breaker when multiple entries match same tier (lower = preferred) |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

**`shared.butler_model_overrides`** — Per-butler tweaks layered on top of the global catalog.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `uuid` PK | |
| `butler_name` | `text` NOT NULL | FK-like reference to butler identity |
| `catalog_entry_id` | `uuid` NULL | FK → `model_catalog.id`. NULL means "add a butler-local entry" |
| `enabled` | `boolean` NULL | Override global enabled. NULL = inherit |
| `priority` | `int` NULL | Override global priority. NULL = inherit |
| `complexity_tier` | `text` NULL | Remap this entry to a different tier for this butler |
| UNIQUE | | `(butler_name, catalog_entry_id)` |

**Why two tables instead of one with a nullable butler column?** Overrides are sparse — most butlers use global defaults. A separate table keeps the common case (global catalog) clean and queryable without filtering, and makes the override semantics explicit (inherit vs. override per field).

**Alternative considered:** Single table with `scope = 'global' | butler_name`. Rejected because NULL-coalesce logic becomes messier and every catalog query needs a WHERE clause.

### 2. Model Resolution Algorithm

At spawn time, the spawner calls `resolve_model(butler_name, complexity_tier)` which:

1. Query `shared.model_catalog` entries for the requested `complexity_tier` where `enabled = true`
2. Left-join `shared.butler_model_overrides` for the specific butler
3. Apply overrides: if override `enabled = false`, exclude; if override remaps tier, respect it; if override changes priority, use it
4. From remaining candidates, pick the one with lowest `priority` value
5. If no candidates, fall back to `butler.toml` `[butler.runtime].model` (backward compat)
6. Return `(runtime_type, model_id, extra_args)`

This is a single SQL query with COALESCE, not multiple round-trips. The spawner then selects the correct `RuntimeAdapter` via `get_adapter(runtime_type)` and passes `model_id` + merged `extra_args` to `invoke()`.

**Important:** The runtime_type from the catalog may differ from the butler's TOML `[runtime].type`. This is intentional — the catalog entry fully specifies which adapter to use. The spawner must be able to instantiate any registered adapter on-demand, not just the one from TOML.

### 3. Complexity Enum and Classification

```python
class Complexity(str, Enum):
    TRIVIAL = "trivial"      # Status checks, simple lookups, confirmations
    MEDIUM = "medium"        # Standard tasks, single-domain work
    HIGH = "high"            # Multi-step reasoning, cross-domain, research
    EXTRA_HIGH = "extra_high"  # Complex analysis, long-horizon planning
```

**Classification points:**

| Trigger path | Who classifies | Default |
|---|---|---|
| External (via Switchboard) | Switchboard LLM classifier (already doing routing) | `medium` on failure |
| Scheduler | `complexity` field in `[[butler.schedule]]` | `medium` |
| Manual trigger (API/MCP) | Optional `complexity` param in `TriggerRequest` | `medium` |
| Tick | Hardcoded | `trivial` |
| Self-trigger | Inherited from parent session or `medium` | `medium` |

**Switchboard classification** piggybacks on the existing LLM routing call. The routing prompt already analyzes the message to decide which butler to send it to — we extend the structured output to also include a `complexity` field. No extra LLM call needed.

**Alternative considered:** Separate heuristic classifier (message length, keyword detection). Rejected because the LLM is already doing semantic analysis for routing; adding one more output field is near-zero marginal cost and much more accurate than heuristics.

### 4. Spawner Changes

Current flow:
```
trigger() → model = self._config.runtime.model → invoke(model=model)
```

New flow:
```
trigger(complexity=...) → (rt, model, args) = resolve_model(name, complexity)
                        → adapter = get_adapter(rt).create_worker()
                        → invoke(model=model, runtime_args=args)
```

Key changes to `Spawner`:
- `trigger()` gains an optional `complexity: Complexity = Complexity.MEDIUM` parameter
- Model resolution happens inside `_run()` just before `invoke()`
- The adapter is selected per-invocation from the catalog's `runtime_type`, not from `self._runtime` (the TOML-configured adapter). This means the spawner needs access to the adapter registry, not a single pre-built adapter.
- Session record stores `model`, `runtime_type`, and `complexity` for observability
- Fallback: if `resolve_model()` returns no candidates, use `self._config.runtime.model` with `self._runtime` (existing behavior)

### 5. Adapter Pool vs. On-Demand Instantiation

The spawner currently creates one adapter instance at init time. With dynamic routing, different invocations may need different adapters.

**Decision:** Lazy adapter pool. The spawner maintains a `dict[str, RuntimeAdapter]` cache, keyed by runtime_type. On first use of a runtime_type, it instantiates via `get_adapter(type).create_worker()`. Subsequent invocations reuse the cached instance. This avoids instantiating all four adapters upfront while still amortizing setup cost.

### 6. Model Aliases as First-Class UI Concept

A model alias is just a row in `model_catalog` — there's no separate "alias" table. The `alias` column is the human-friendly name, `model_id` is the actual model string, and `extra_args` captures the runtime-specific CLI arguments.

The dashboard UI presents this as an alias editor:
- **Name**: e.g. `gpt-5.4-high`
- **Base model**: e.g. `gpt-5.4` (dropdown of known models, or free-text)
- **Runtime**: e.g. `codex` (dropdown from adapter registry)
- **Extra args**: e.g. `--config model_reasoning_effort="high"` (key-value editor or raw JSON)
- **Complexity tier**: e.g. `high` (dropdown)
- **Enabled**: toggle

**Seed data** (inserted by migration):

| Alias | Runtime | Model ID | Extra Args | Tier |
|-------|---------|----------|------------|------|
| `claude-haiku` | `claude` | `claude-haiku-4-5-20251001` | `[]` | `trivial` |
| `claude-sonnet` | `claude` | `claude-sonnet-4-6` | `[]` | `medium` |
| `claude-opus` | `claude` | `claude-opus-4-6` | `[]` | `high` |
| `gpt-5.1` | `codex` | `gpt-5.1` | `[]` | `medium` |
| `gpt-5.3-spark` | `codex` | `gpt-5.3-codex-spark` | `[]` | `trivial` |
| `gpt-5.4` | `codex` | `gpt-5.4` | `[]` | `high` |
| `gpt-5.4-high` | `codex` | `gpt-5.4` | `["--config", "model_reasoning_effort=high"]` | `extra_high` |
| `gemini-2.5-flash` | `gemini` | `gemini-2.5-flash` | `[]` | `trivial` |
| `gemini-2.5-pro` | `gemini` | `gemini-2.5-pro` | `[]` | `high` |
| `minimax-m2.5` | `opencode` | `minimax/MiniMax-M2.5` | `[]` | `medium` |
| `glm-5` | `opencode` | `zhipu/GLM-5` | `[]` | `medium` |
| `kimi-k2.5` | `opencode` | `moonshot/Kimi-K2.5` | `[]` | `high` |

### 7. Dashboard API Design

**Settings endpoints** (new router at `/api/settings/models`):
- `GET /api/settings/models` — List all catalog entries
- `POST /api/settings/models` — Create catalog entry
- `PUT /api/settings/models/{id}` — Update catalog entry
- `DELETE /api/settings/models/{id}` — Delete catalog entry

**Per-butler override endpoints** (extend existing butler routes):
- `GET /api/butlers/{name}/model-overrides` — List overrides for butler
- `PUT /api/butlers/{name}/model-overrides` — Upsert overrides (batch)
- `DELETE /api/butlers/{name}/model-overrides/{id}` — Remove override

### 8. Scheduler Extension

The `[[butler.schedule]]` TOML section gains an optional `complexity` field:

```toml
[[butler.schedule]]
name = "daily-digest"
cron = "0 8 * * *"
prompt = "Generate the daily digest"
complexity = "high"  # Optional, defaults to "medium"
```

The `scheduled_tasks` DB table gains a `complexity` column (`text`, nullable, default `medium`). `sync_schedules()` picks it up and `dispatch_due_tasks()` passes it through to `spawner.trigger(complexity=...)`.

### 9. Switchboard Routing Extension

The Switchboard's routing LLM output schema gains a `complexity` field alongside the existing `target_butler` and `sub_prompt` fields. The structured output becomes:

```json
{
  "segments": [
    {
      "target_butler": "finance",
      "sub_prompt": "...",
      "complexity": "medium"
    }
  ]
}
```

The `route_to_butler` MCP tool call (or internal dispatch) passes `complexity` through to the target butler's `trigger()`.

## Risks / Trade-offs

**[Cold-start: empty catalog]** → Migration seeds defaults. Fallback to TOML config ensures zero downtime if catalog is empty or DB unreachable.

**[Extra DB query per spawn]** → One lightweight SELECT with JOIN per invocation. Acceptable latency (~1-5ms). Could cache with short TTL if needed, but premature for now.

**[Switchboard misclassification]** → Worst case: wrong model tier for a task. Impact is cost/quality, not correctness. Medium default is a safe fallback. Dashboard shows which model was selected per session for debugging.

**[Runtime adapter mismatch]** → A catalog entry could reference a runtime type whose binary isn't installed. The adapter pool instantiation will fail at first use. Mitigation: health check on startup validates that all enabled catalog entries have corresponding adapters with binaries on PATH.

**[Breaking change: TOML model ignored]** → Gradual rollout. TOML model becomes the fallback. Butlers work identically until catalog entries are populated. No flag day.

**[Alias naming collisions]** → UNIQUE constraint on `alias` column prevents duplicates. UI validates before submission.

## Migration Plan

1. **Phase 1 — Schema + seed data**: Alembic migration creates tables, inserts seed catalog entries. No behavior change yet.
2. **Phase 2 — Spawner resolution**: Spawner gains `resolve_model()`. Falls back to TOML if catalog empty. Existing behavior preserved.
3. **Phase 3 — Complexity plumbing**: Add `complexity` param to `trigger()`, scheduler, and Switchboard routing output. Everything defaults to `medium`.
4. **Phase 4 — Dashboard UI**: Settings page for catalog CRUD. Per-butler override UI on butler config pages.
5. **Phase 5 — Switchboard classifier**: Extend routing prompt to output complexity. Only behavioral change.

Phases 1-3 can be deployed together. Phase 4 and 5 can follow independently.

**Rollback:** Drop the two shared tables. Spawner fallback ensures all butlers revert to TOML config automatically.

## Open Questions

1. **Should complexity be exposed to the butler's LLM session?** If a butler knows it was spawned as "trivial", it could self-limit. But this might cause unexpected behavioral changes.
2. **Multi-model fallback chains** — If the preferred model for a tier is unavailable (API error, rate limit), should we automatically try the next-priority entry? Adds complexity but improves resilience.
3. **Cost tracking** — Should the catalog store per-model cost rates for dashboard cost attribution, or is that a separate concern?
