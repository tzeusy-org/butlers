## Context

Butler operational config (tool surface, model, concurrency, timeouts) is currently hardcoded in `butler.toml` and loaded once at daemon startup. There is no runtime mutation path — every change requires a toml edit and redeploy. The `[butler.seed_configs]` section exists in every toml but is never parsed. The `core_groups` field (controlling tool surface) was silently dropped by `load_config()` and never took effect in production.

The dashboard config tab (`/butlers/{name}?tab=config`) is read-only, displaying the raw toml.

### Current config resolution chain

```
butler.toml → load_config() → ButlerConfig.runtime → Spawner (static)
                                                    → _register_core_tools (static)
```

Model has a separate override chain: `butler_model_overrides → model_catalog → toml fallback`.

### Desired state

```
butler.toml [runtime_seed]  ──seed-if-empty──▶  {schema}.runtime_config (DB)
                                                      │
                                              ┌───────┴──────────┐
                                              │                  │
                                         HOT fields          COLD fields
                                     (per-spawn, 30s TTL)  (restart required)
                                              │                  │
                                         Spawner            MCP tool registration
                                     model, runtime_type    core_groups
                                     args, timeout          max_concurrent/queued
```

## Goals / Non-Goals

**Goals:**
- `butler.toml` is the seed source only — used on first boot, ignored thereafter
- Per-schema `runtime_config` table is the single runtime source of truth
- Dashboard can read and edit runtime config without redeploying
- Hot fields (model, runtime_type, args, session_timeout_s) take effect within 30s
- Cold fields (core_groups, concurrency limits) clearly documented as restart-required
- Clean merge of `[butler.runtime]` and `[butler.seed_configs]` into `[butler.runtime_seed]`

**Non-Goals:**
- Hot-reloading `core_groups` (requires MCP tool re-registration — too complex for v1)
- Hot-reloading concurrency limits (asyncio.Semaphore capacity is immutable)
- Consolidating `butler_model_overrides` / `model_catalog` into this table (existing system stays; runtime_config.model is the fallback)
- Dashboard UI for `liveness_ttl_seconds` / `route_contract_min/max` (these stay in `switchboard.butler_registry`, managed via switchboard registration)

## Decisions

### 0. Identity vs operational tuning (doctrine amendment)

**Decision:** Amend Non-Negotiable Rule #5 in `about/heart-and-soul/vision.md` to formally distinguish *identity* from *operational tuning*.

- **Identity** (git-controlled): name, port, description, type, manifesto, module declarations, schedule definitions, personality (CLAUDE.md). These define *what* the butler is.
- **Operational tuning** (DB-persisted, dashboard-managed): model, runtime_type, core_groups, concurrency limits, session timeouts, CLI args. These define *how* the butler behaves right now.

**Why:** The original rule ("if it is not in git, it is not part of who the butler is") conflates two concerns. A butler's identity — its domain, personality, and module surface — changes rarely and should be reviewed in PRs. A butler's operational parameters — which model to use, how many concurrent sessions, which tool groups to expose — are tuning knobs that need to move at runtime speed, especially for model performance experiments (gpt-5.4-mini struggles with large tool surfaces). Moving these to DB with git-based seed values preserves the "git as initial truth" principle while enabling dashboard-speed iteration.

**Alternative rejected:** Keep `core_groups` git-only, move only model/concurrency to DB. Rejected because tool surface tuning is the primary motivation for this change — it's the field that most urgently needs runtime iteration.

### 1. Dedicated table vs state store KV

**Decision:** Dedicated `{schema}.runtime_config` table with typed columns.

**Why not state store:**
- State store is untyped JSONB — no schema enforcement, no column-level defaults
- No way to distinguish "not set" from "set to null" in JSONB
- Dashboard would need to know the magic key prefix convention
- Typed columns are self-documenting, queryable, and have DB-level defaults

### 2. Single-row table keyed by butler_name

**Decision:** One row per schema, `butler_name text PRIMARY KEY`.

```sql
CREATE TABLE {schema}.runtime_config (
    butler_name         text PRIMARY KEY,
    core_groups         text[],
    model               text,
    runtime_type        text NOT NULL DEFAULT 'codex',
    args                jsonb NOT NULL DEFAULT '[]'::jsonb,
    max_concurrent      int NOT NULL DEFAULT 3,
    max_queued          int NOT NULL DEFAULT 10,
    session_timeout_s   int NOT NULL DEFAULT 900,
    seeded_at           timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
```

`butler_name` as PK (rather than synthetic id) makes cross-schema queries and debugging easier. There is exactly one row per schema.

### 3. TTL-cached accessor for hot reload

**Decision:** `RuntimeConfigAccessor` class with 30-second TTL cache.

```python
class RuntimeConfigAccessor:
    def __init__(self, pool: asyncpg.Pool, schema: str, ttl_s: float = 30.0): ...
    async def get(self) -> RuntimeConfig: ...          # cached read
    async def seed_if_empty(self, seed: RuntimeSeedConfig) -> RuntimeConfig: ...
```

**Why TTL over event-driven:**
- No pub/sub infrastructure needed
- 30s staleness is acceptable (these are operational tuning knobs, not latency-critical)
- Zero additional dependencies
- Spawner already does DB reads per-trigger (model catalog), so one more cached read is negligible

**Why not periodic background refresh:**
- TTL-on-access is simpler — no background tasks to manage, no shutdown cleanup
- If no sessions are spawning, no DB reads happen (natural backpressure)

### 4. Hot vs cold field split

**Decision:** Fields are hot (per-spawn) or cold (restart-required) based on where they're consumed:

| Field | Tier | Reason |
|-------|------|--------|
| `model` | HOT | Spawner reads per-trigger, already has catalog fallback chain |
| `runtime_type` | HOT | Spawner reads per-trigger for adapter selection |
| `args` | HOT | Spawner reads per-trigger for CLI args |
| `session_timeout_s` | HOT | Spawner reads per-trigger for asyncio.wait_for |
| `core_groups` | COLD | Consumed at MCP tool registration time (startup only) |
| `max_concurrent` | COLD | Baked into asyncio.Semaphore at Spawner construction |
| `max_queued` | COLD | Read at Spawner construction for queue limit |

The dashboard PATCH endpoint SHALL return a response indicating which changed fields require restart.

### 5. Toml section rename strategy

**Decision:** `[butler.runtime]` → `[butler.runtime_seed]`, `[butler.seed_configs]` merged in.

The config parser SHALL:
1. Accept `[butler.runtime_seed]` as the canonical section
2. Reject `[butler.runtime]` and `[butler.seed_configs]` with a clear error message pointing to the rename
3. Parse all fields into a `RuntimeSeedConfig` dataclass (not `RuntimeConfig` — the seed is not the runtime config)

### 6. Liveness TTL and route contracts stay in butler_registry

**Decision:** `liveness_ttl_seconds`, `route_contract_min`, `route_contract_max` are NOT added to `runtime_config`.

These are consumed by the switchboard's routing code, not by the butler daemon itself. They already live in `switchboard.butler_registry` and are seeded during butler registration. Adding them to per-butler `runtime_config` creates a sync problem with no benefit.

The `[butler.runtime_seed]` section still contains these fields for seeding the registry on first registration. The daemon passes them to `register_butler()` at startup.

### 7. Re-seeding mechanism

**Decision:** Delete the row and restart.

```sql
DELETE FROM {schema}.runtime_config WHERE butler_name = '{name}';
-- Then restart the daemon — it will re-seed from toml
```

No `--reseed` flag or toml override for v1. This is a rare administrative action.

## Risks / Trade-offs

- **[Risk] Config drift between toml and DB** → The toml is documentation-only after first boot. If someone edits the toml expecting it to take effect, it won't. **Mitigation:** Clear `[butler.runtime_seed]` naming signals "seed" semantics. Daemon logs "Using runtime config from DB (seeded at ...)" on startup. Dashboard shows the effective config.

- **[Risk] 30s staleness for hot fields** → A model change via dashboard takes up to 30s to affect new sessions. **Mitigation:** Acceptable for operational tuning. Could add a cache-bust endpoint later if needed.

- **[Risk] Cold field confusion** → User changes `core_groups` via dashboard, expects immediate effect. **Mitigation:** PATCH response includes `restart_required: ["core_groups"]` for changed cold fields. Dashboard UI shows a restart-required badge.

- **[Risk] Migration on existing deployments** → All butlers need their runtime_config table seeded on first boot after upgrade. **Mitigation:** Alembic migration creates the table. Daemon `start()` handles seeding via `seed_if_empty()`. No manual intervention needed.

- **[Trade-off] Toml still required for structural config** → We can't eliminate the toml entirely (name, port, db schema, modules are structural). This is intentional — structural config defines identity, operational config defines behavior.

## Migration Plan

1. **Alembic migration**: Create `runtime_config` table in all butler schemas
2. **Config parser**: Add `_parse_runtime_seed()`, deprecate `_parse_runtime()` and `seed_configs` parsing
3. **Daemon boot**: Add `RuntimeConfigAccessor` creation and `seed_if_empty()` call before tool registration
4. **Spawner**: Accept accessor, read hot fields per-spawn
5. **Dashboard API**: Add GET/PATCH endpoints
6. **Toml rename**: Update all `roster/*/butler.toml` files
7. **Frontend**: Update config tab UI
8. **Tests**: Update config parsing, daemon boot, spawner tests

**Rollback:** Revert the Alembic migration (drops table), revert code changes. Butlers fall back to toml-only config. The toml files still contain all values since the seed section is a superset.

## Resolved Questions

- **Should the PATCH endpoint validate `core_groups` values?** Yes. The PATCH endpoint SHALL reject unknown group names with HTTP 422. Known groups are: `infra`, `state`, `scheduling`, `sessions`, `notifications`, `media`, `temporal`, `module_mgmt`, `switchboard_routing`, `switchboard_backfill`. This prevents typos from silently disabling tool groups.
- **Should we emit a Prometheus metric for accessor staleness?** No, not for v1. The 30s TTL is deterministic and observable from existing spawn latency metrics. If staleness becomes a problem, add a gauge later.
