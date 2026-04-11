# Model Catalog Timeout And Runtime Config Reduction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `public.model_catalog` the only source of truth for runtime selection and per-session timeout, while reducing per-butler `runtime_config` to operational controls only.

**Architecture:** Add `session_timeout_s` to `model_catalog`, flow it through model resolution into `Spawner`, and remove overlapping runtime-selection fields from `runtime_config`. Keep healing/QA watchdog limits separate as outer workflow caps.

**Tech Stack:** Python, FastAPI, asyncpg, Pydantic, React, TypeScript, pytest

---

### Task 1: Add failing backend tests for the new catalog timeout contract

**Files:**
- Modify: `tests/core/test_model_routing.py`
- Modify: `tests/core/test_core_spawner.py`
- Modify: `tests/api/test_model_settings.py`

**Step 1: Write failing tests**

- Add a model-routing test asserting `resolve_model()` returns `session_timeout_s`.
- Add a spawner test asserting a resolved catalog row controls `runtime.invoke(timeout=...)`.
- Add model-settings API tests asserting catalog create/update/list payloads include `session_timeout_s`.

**Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/core/test_model_routing.py tests/core/test_core_spawner.py tests/api/test_model_settings.py -q
```

**Step 3: Implement the minimal backend changes to make them pass**

Touch:
- `src/butlers/core/model_routing.py`
- `src/butlers/core/spawner.py`
- `src/butlers/api/routers/model_settings.py`

**Step 4: Re-run the same tests**

**Step 5: Commit if this were a standalone slice**


### Task 2: Add failing tests for the reduced runtime-config surface

**Files:**
- Modify: `tests/core/test_runtime_config.py`
- Modify: `tests/api/test_runtime_config.py`
- Modify: `tests/migrations/test_runtime_config_migration.py`

**Step 1: Write failing tests**

- Assert runtime-config rows/models no longer expose `model`, `runtime_type`, `args`, or `session_timeout_s`.
- Assert the runtime-config migration schema only keeps `butler_name`, `core_groups`, `max_concurrent`, `max_queued`, timestamps.

**Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/core/test_runtime_config.py tests/api/test_runtime_config.py tests/migrations/test_runtime_config_migration.py -q
```

**Step 3: Implement the minimal backend changes**

Touch:
- `src/butlers/core/runtime_config.py`
- `src/butlers/api/routers/runtime_config.py`
- migration file under `alembic/versions/core/`
- `src/butlers/config.py`

**Step 4: Re-run the same tests**


### Task 3: Add migration coverage for model catalog timeout and runtime-config reduction

**Files:**
- Create/Modify: core Alembic migration(s)
- Modify: relevant migration tests

**Step 1: Write/extend failing migration tests**

- Expect `model_catalog.session_timeout_s INT NOT NULL DEFAULT 1800`.
- Expect existing runtime-config migration expectations to match the reduced shape.

**Step 2: Run the focused migration tests and watch them fail**

**Step 3: Implement migration(s)**

- Add a forward migration to add/backfill `model_catalog.session_timeout_s`.
- Add a forward migration to drop runtime-config runtime-selection columns.

**Step 4: Re-run migration tests**


### Task 4: Update frontend contracts and UI surfaces

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/settings/ModelCatalogCard.tsx`
- Modify: `frontend/src/components/butler-detail/RuntimeConfigCard.tsx`

**Step 1: Write failing frontend-facing tests if they exist; otherwise rely on type/build verification**

**Step 2: Implement UI changes**

- Add editable `session_timeout_s` to the model catalog card.
- Remove `model`, `runtime_type`, and `session_timeout_s` from the runtime-config card.

**Step 3: Run targeted frontend/type verification**


### Task 5: Verify integrated behavior

**Files:**
- No new files expected

**Step 1: Run focused backend tests**

```bash
uv run pytest tests/core/test_model_routing.py tests/core/test_core_spawner.py tests/core/test_runtime_config.py tests/api/test_model_settings.py tests/api/test_runtime_config.py tests/migrations/test_runtime_config_migration.py -q
```

**Step 2: Run focused lint/type checks on changed files if needed**

```bash
uv run ruff check src/butlers/core/model_routing.py src/butlers/core/spawner.py src/butlers/core/runtime_config.py src/butlers/api/routers/model_settings.py src/butlers/api/routers/runtime_config.py tests/core/test_model_routing.py tests/core/test_core_spawner.py tests/core/test_runtime_config.py tests/api/test_model_settings.py tests/api/test_runtime_config.py tests/migrations/test_runtime_config_migration.py
```

**Step 3: Report actual verification results with evidence**

