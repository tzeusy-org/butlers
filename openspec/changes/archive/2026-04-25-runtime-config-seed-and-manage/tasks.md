## 1. Doctrine and Design Contract Amendments

- [x] 1.1 Update Non-Negotiable Rule #5 in `about/heart-and-soul/vision.md` to distinguish identity (git-controlled) from operational tuning (DB-persisted)
- [x] 1.2 Amend RFC 0002 §Tool Budget Discipline to replace tier-based gating with `core_groups` mechanism (note: route.execute LLM-visibility filtering deferred)
- [x] 1.3 Amend RFC 0001 §Startup Phases to add phase 9b (resolve runtime config from DB)

## 2. Database Schema

- [x] 2.1 Create Alembic migration adding `runtime_config` table to all butler schemas (columns: butler_name PK, core_groups text[], model text, runtime_type text, args jsonb, max_concurrent int, max_queued int, session_timeout_s int, seeded_at timestamptz, updated_at timestamptz)
- [x] 2.2 Write migration test verifying table exists in each schema after migration

## 3. Config Parser Rename

- [x] 3.1 Add `RuntimeSeedConfig` dataclass to `config.py` with fields: core_groups, model, runtime_type, args, max_concurrent_sessions, max_queued_sessions, session_timeout_s, liveness_ttl_seconds, route_contract_min, route_contract_max
- [x] 3.2 Create `_parse_runtime_seed()` function parsing `[butler.runtime_seed]` into `RuntimeSeedConfig`
- [x] 3.3 Update `load_config()` to call `_parse_runtime_seed()` and store result on `ButlerConfig.runtime_seed`
- [x] 3.4 Add error detection for old `[butler.runtime]` and `[butler.seed_configs]` sections with clear rename instructions
- [x] 3.5 Handle missing `[butler.runtime_seed]` section gracefully — return `RuntimeSeedConfig` with all defaults
- [x] 3.6 Update all `roster/*/butler.toml` files: rename `[butler.runtime]` to `[butler.runtime_seed]`, merge `[butler.seed_configs]` fields into it, remove `[butler.seed_configs]` section
- [x] 3.7 Update config parser tests for new section name, rejection of old names, missing section defaults, and RuntimeSeedConfig parsing

## 4. RuntimeConfigAccessor

- [x] 4.1 Create `RuntimeConfigAccessor` class in `src/butlers/core/runtime_config.py` with `__init__(pool, schema, ttl_s=30.0)`, `async get() -> RuntimeConfig`, and `async seed_if_empty(seed: RuntimeSeedConfig) -> RuntimeConfig`
- [x] 4.2 Implement TTL-cached `get()` — query DB if cache expired, return stale cache on DB failure (with warning log), raise if no prior cache
- [x] 4.3 Implement `seed_if_empty()` — `INSERT ... ON CONFLICT DO NOTHING` from seed values, return the (existing or new) row
- [x] 4.4 Write unit tests: cache hit within TTL, cache miss after TTL, seed on empty table, no-op seed on existing row, re-seed after row deletion, DB failure with stale cache, DB failure with no cache (fatal), concurrent seed race

## 5. Daemon Boot Sequence

- [x] 5.1 Update `daemon.start()` to create `RuntimeConfigAccessor` after DB provisioning (between phases 8 and 10)
- [x] 5.2 Call `accessor.seed_if_empty(config.runtime_seed)` before tool registration
- [x] 5.3 Pass effective `RuntimeConfig` (from accessor) to `_register_core_tools()` for `core_groups` gating
- [x] 5.4 Enforce name-gating for switchboard-only and messenger-only tools: `switchboard_routing`/`switchboard_backfill` groups only effective when `butler_name == "switchboard"`, messenger delivery tools only when `butler_name == "messenger"`. Log warning for ineffective group inclusions.
- [x] 5.5 Ensure `route.execute` is always registered regardless of `core_groups`
- [x] 5.6 Remove the `_tools_to_remove` post-registration pruning section entirely (replaced by core_groups mechanism)
- [x] 5.7 Remove `UNIVERSAL_CORE_TOOL_NAMES`, `DOMAIN_CORE_TOOL_NAMES`, `MESSENGER_CORE_TOOL_NAMES` tier constants (replaced by group system)
- [x] 5.8 Pass `RuntimeConfigAccessor` to Spawner constructor
- [x] 5.9 Update daemon boot logging to indicate config source (seeded vs DB, with timestamps)
- [x] 5.10 Update daemon startup tests

## 6. Spawner Hot Reload

- [x] 6.1 Add `RuntimeConfigAccessor` parameter to Spawner `__init__`
- [x] 6.2 In `trigger()`, read `accessor.get()` for model fallback, runtime_type, args, session_timeout_s instead of `self._config.runtime.*`
- [x] 6.3 At construction, read `accessor.get().max_concurrent` for semaphore sizing and `max_queued` for queue limit
- [x] 6.4 Update spawner tests: mock accessor, verify hot fields read per-trigger, verify cold fields read once at construction, verify stale cache fallback on DB failure

## 7. Dashboard API

- [x] 7.1 Create Pydantic models for `RuntimeConfigResponse` and `RuntimeConfigPatch` in `src/butlers/api/routers/runtime_config.py`
- [x] 7.2 Implement `GET /api/butlers/{name}/runtime-config` endpoint reading from `{schema}.runtime_config`, including `field_tiers` map in response
- [x] 7.3 Implement `PATCH /api/butlers/{name}/runtime-config` endpoint with field validation (reject negative concurrency, unknown core_group names), `updated_at` update, and `restart_required` list in response
- [x] 7.4 Define known core_groups constant: `infra`, `state`, `scheduling`, `sessions`, `notifications`, `media`, `temporal`, `module_mgmt`, `switchboard_routing`, `switchboard_backfill`
- [x] 7.5 Register router in `src/butlers/api/app.py`
- [x] 7.6 Write API endpoint tests: GET success/404, PATCH hot field, PATCH cold field, PATCH unknown core_group (422), PATCH empty body (200 no-op), PATCH negative concurrency (422)

## 8. Dashboard Frontend

- [x] 8.1 Add `useRuntimeConfig()` hook fetching from `GET /api/butlers/{name}/runtime-config`
- [x] 8.2 Add `RuntimeConfigCard` component to the butler config tab displaying editable fields with hot/cold indicators
- [x] 8.3 Add core_groups multi-select editor with known group names (no free-text)
- [x] 8.4 Implement save handler calling PATCH endpoint, showing restart-required notification for cold fields
- [x] 8.5 Replace raw toml display in config tab with the new RuntimeConfigCard for operational fields (keep structural toml display for identity/module config)

## 9. Cleanup

- [x] 9.1 Remove the `_parse_runtime()` function and `RuntimeConfig` construction from `load_config()` (replaced by `_parse_runtime_seed()`)
- [x] 9.2 Remove `ButlerConfig.runtime` field (replaced by `ButlerConfig.runtime_seed` + accessor)
- [x] 9.3 Update all daemon/spawner code that reads `self.config.runtime.*` for structural fields to use the appropriate new source
- [x] 9.4 Run full test suite, fix any remaining references to old config paths
- [x] 9.5 Update CLAUDE.md with new config architecture notes
- [x] 9.6 Update `about/lay-and-land/data-flow.md` to document the new config data path: dashboard → runtime_config table → accessor (TTL cache) → spawner/tool registration
