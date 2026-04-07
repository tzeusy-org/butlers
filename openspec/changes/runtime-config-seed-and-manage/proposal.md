## Why

Butler runtime config (`core_groups`, `model`, `runtime_type`, `max_concurrent_sessions`, etc.) is currently baked into `butler.toml` and read at daemon startup. There is no way to tune these values without editing the toml and redeploying. The `core_groups` allowlist — which controls tool surface and directly impacts model performance (especially gpt-5.4-mini) — was found to have never worked in production because `load_config()` silently dropped the field. Meanwhile, `[butler.seed_configs]` exists in every toml but is never parsed by any code. The config tab on the dashboard is read-only.

This change makes `butler.toml` the seed source for runtime config (used only on first bootstrap), with a dedicated per-schema DB table as the runtime source of truth, editable via the dashboard.

**Doctrine amendment:** This change refines Non-Negotiable Rule #5 in `about/heart-and-soul/vision.md` to distinguish *identity* (git-controlled: name, manifesto, modules, schedules) from *operational tuning* (DB-persisted: model, core_groups, concurrency, timeouts). Identity answers "what is this butler?"; operational tuning answers "how should it behave right now?" The seed values in git provide initial defaults; the database is authoritative after first boot.

## What Changes

- **BREAKING**: Rename `[butler.runtime]` to `[butler.runtime_seed]` and merge `[butler.seed_configs]` fields into it. The old section names are no longer parsed.
- New `{schema}.runtime_config` table per butler — single-row, typed columns for all operational config fields.
- Daemon boot seeds the table from toml on first run; subsequent boots read from DB and ignore the toml seed.
- `RuntimeConfigAccessor` with TTL-based caching: hot fields (model, runtime_type, args, session_timeout_s) take effect within 30s without restart; cold fields (core_groups, max_concurrent_sessions, max_queued_sessions) require restart.
- Dashboard API: `GET/PATCH /api/butlers/{name}/runtime-config` for reading and updating the DB-backed config.
- Spawner reads hot config per-spawn via the accessor instead of the static `ButlerConfig`.
- Remove the `core_groups` bug fix in `load_config()` (no longer needed — toml is seed-only).

## Capabilities

### New Capabilities
- `runtime-config-table`: Per-schema `runtime_config` table, Alembic migration, accessor with TTL cache, seed-if-empty logic
- `runtime-config-api`: Dashboard API endpoints for reading and patching runtime config
- `runtime-config-dashboard-ui`: Config tab UI for editing operational runtime settings (core_groups, model, concurrency, timeouts)

### Modified Capabilities
- `core-daemon`: Boot sequence changes — seed from toml on first run, read from DB on subsequent boots. Spawner uses accessor for hot fields. `_register_core_tools` reads `core_groups` from DB. Config section rename (`[butler.runtime]` → `[butler.runtime_seed]`).
- `core-spawner`: Reads model, runtime_type, args, session_timeout_s from `RuntimeConfigAccessor.get()` per-spawn instead of static config.

## Impact

- **Config files**: All `roster/*/butler.toml` files need section rename (`[butler.runtime]` → `[butler.runtime_seed]`, merge `[butler.seed_configs]`).
- **Config parser**: `src/butlers/config.py` — `_parse_runtime()` replaced with `_parse_runtime_seed()`, `load_config()` returns `RuntimeSeedConfig` instead of `RuntimeConfig` for the runtime field.
- **Daemon**: `src/butlers/daemon.py` — boot sequence, tool registration, spawner construction all change to use accessor.
- **Spawner**: `src/butlers/core/spawner.py` — accepts `RuntimeConfigAccessor`, reads hot fields per-spawn.
- **Database**: New Alembic migration creating `runtime_config` table in every butler schema.
- **Dashboard API**: New endpoints in `src/butlers/api/routers/`.
- **Frontend**: Config tab component changes in `frontend/src/components/butler-detail/`.
- **Tests**: Config loading tests, daemon boot tests, spawner tests all need updates.
