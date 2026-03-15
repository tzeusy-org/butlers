## 1. Database Schema & Seed Data

- [ ] 1.1 Create Alembic migration adding `shared.model_catalog` table (id, alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority, created_at, updated_at) with UNIQUE on alias and CHECK on complexity_tier
- [ ] 1.2 Create Alembic migration adding `shared.butler_model_overrides` table (id, butler_name, catalog_entry_id FK, enabled, priority, complexity_tier) with UNIQUE on (butler_name, catalog_entry_id) and CASCADE delete on catalog_entry_id
- [ ] 1.3 Add seed data insert to migration: 12 default catalog entries (claude-haiku/sonnet/opus, gpt-5.1/5.3-spark/5.4/5.4-high, gemini-2.5-flash/pro, minimax-m2.5, glm-5, kimi-k2.5) with ON CONFLICT DO NOTHING
- [ ] 1.4 Add `complexity` column (text, nullable, default 'medium') to `scheduled_tasks` table via migration

## 2. Complexity Enum & Model Resolution

- [ ] 2.1 Define `Complexity` enum (trivial, medium, high, extra_high) in a new `src/butlers/core/model_routing.py` module
- [ ] 2.2 Implement `resolve_model(pool, butler_name, complexity_tier)` — single SQL query with LEFT JOIN on overrides, COALESCE for nullable override fields, ORDER BY effective priority then created_at, returns `(runtime_type, model_id, extra_args)` or None
- [ ] 2.3 Write tests for `resolve_model`: global-only resolution, override disabling, override tier remap, override priority change, no-candidates-returns-None, priority tie-breaking by created_at

## 3. Spawner Changes

- [ ] 3.1 Add `complexity: Complexity = Complexity.MEDIUM` parameter to `Spawner.trigger()` signature
- [ ] 3.2 Add lazy adapter pool (`dict[str, RuntimeAdapter]`) to Spawner, initialized with the TOML adapter; add `_get_or_create_adapter(runtime_type)` method
- [ ] 3.3 Replace static `model = self._config.runtime.model` in `_run()` with `resolve_model()` call; fall back to TOML on None; select adapter from pool based on resolved runtime_type
- [ ] 3.4 Merge catalog `extra_args` with TOML `args` when both present (TOML args first, catalog args appended)
- [ ] 3.5 Pass `complexity` and resolution source (`catalog`/`toml_fallback`) to `session_create()` for observability
- [ ] 3.6 Write tests for spawner: catalog resolution path, TOML fallback path, adapter pool lazy instantiation, extra_args merging

## 4. Scheduler Changes

- [ ] 4.1 Add `complexity` to `_normalize_schedule_dispatch()` validation — accept valid Complexity enum values, default to medium
- [ ] 4.2 Update `sync_schedules()` to read `complexity` from TOML entries and include in change-detection comparison
- [ ] 4.3 Update `dispatch_due_tasks()` to pass `complexity` to `dispatch_fn(complexity=...)` for prompt-mode tasks
- [ ] 4.4 Update `schedule_create()` and `schedule_update()` to accept and persist `complexity` field
- [ ] 4.5 Write tests: TOML sync with complexity, dispatch passes complexity, CRUD accepts complexity, invalid complexity rejected

## 5. Switchboard Routing Changes

- [ ] 5.1 Extend Switchboard routing prompt/structured output schema to include `complexity` field per segment
- [ ] 5.2 Add complexity classification guidelines to the routing prompt (trivial/medium/high/extra_high signal descriptions)
- [ ] 5.3 Update routing output parser to extract `complexity` per segment, defaulting to `medium` on missing/invalid values
- [ ] 5.4 Add `complexity` to `route.v1` envelope `input` section in dispatch construction
- [ ] 5.5 Update `route.execute` handler to extract `complexity` from envelope and pass to `spawner.trigger(complexity=...)`
- [ ] 5.6 Update deterministic triage (rule-based, thread affinity) paths to set complexity to `medium`
- [ ] 5.7 Write tests: routing output includes complexity, envelope carries complexity, handler extracts complexity, triage default

## 6. Dashboard API — Model Settings

- [ ] 6.1 Create `src/butlers/api/routers/model_settings.py` with CRUD endpoints: GET/POST `/api/settings/models`, PUT/DELETE `/api/settings/models/{id}`
- [ ] 6.2 Create Pydantic models for catalog entry request/response schemas
- [ ] 6.3 Add butler override endpoints: GET `/api/butlers/{name}/model-overrides`, PUT (batch upsert), DELETE `/{id}`
- [ ] 6.4 Add `complexity` field to `TriggerRequest` model (optional, default medium)
- [ ] 6.5 Add `GET /api/butlers/{name}/resolve-model?complexity=X` preview endpoint for UI model preview
- [ ] 6.6 Wire trigger API to pass complexity to spawner
- [ ] 6.7 Write tests for all new API endpoints: catalog CRUD, override CRUD, trigger with complexity, resolve preview

## 7. Dashboard UI — Settings Page

- [ ] 7.1 Add "Models" section to settings page with catalog table grouped by complexity tier
- [ ] 7.2 Build "Add/Edit Model" dialog with fields: alias, runtime type dropdown, model ID, extra args editor (key-value + raw JSON toggle), complexity tier dropdown, priority, enabled toggle
- [ ] 7.3 Add "Use template" dropdown in dialog with presets for Codex reasoning effort and Claude extended thinking
- [ ] 7.4 Add inline enabled toggle and delete-with-cascade-warning on catalog rows
- [ ] 7.5 Wire TanStack Query mutations for catalog CRUD with invalidation and toast feedback

## 8. Dashboard UI — Butler Pages

- [ ] 8.1 Add complexity tier badge column to schedule table in Schedules tab
- [ ] 8.2 Add complexity dropdown to schedule create/edit dialog
- [ ] 8.3 Add complexity dropdown to Trigger tab with default Medium
- [ ] 8.4 Add resolved model preview below complexity dropdown (calls resolve-model endpoint reactively)
- [ ] 8.5 Add complexity tier badge to trigger history entries
- [ ] 8.6 Add model and complexity columns to Sessions tab table
- [ ] 8.7 Add model resolution metadata (alias, runtime type, complexity, resolution source) to session detail drawer
- [ ] 8.8 Add per-butler model override section (effective models per tier table with override/reset actions)

## 9. Integration & Validation

- [ ] 9.1 End-to-end test: trigger butler with complexity=high, verify correct model resolved from catalog and recorded in session
- [ ] 9.2 End-to-end test: scheduler fires task with complexity=high, verify model resolution
- [ ] 9.3 Verify TOML fallback works when catalog is empty (no regression)
- [ ] 9.4 Verify Switchboard routing classifies complexity and propagates through route.v1 envelope
- [ ] 9.5 Run full test suite and lint checks
