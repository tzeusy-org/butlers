## Why

Every butler currently spawns sessions with a single hardcoded model from `butler.toml`. This is wasteful — a "check my calendar" query costs the same as a complex multi-step research task. With multiple vendors now supported (Claude, Codex/OpenAI, Gemini), we need the system to pick the right model for the job: cheaper/faster models for trivial work, heavier models for complex tasks.

Moving model selection from static config to a runtime decision driven by task complexity also enables cost control, vendor diversity, and per-butler model policies — all manageable from the dashboard without redeploying config files.

## What Changes

- **Model catalog in `shared` schema** — A new `shared.model_catalog` table defining available vendor+model combinations with their complexity tier, runtime type, and optional extra args. Supports user-defined "model aliases" — named configurations like `gpt-5.4-high` that combine a base model (`gpt-5.4`) with extra runtime args (`--config model_reasoning_effort="high"`). Aliases are fully manageable through the dashboard UI: create, edit, delete, with sane seed defaults for common configurations (e.g. Codex reasoning effort levels, Claude extended thinking).
- **Complexity tiers** — An enum of `trivial | medium | high | extra_high`. Each model entry is tagged with the complexity tier(s) it serves.
- **Global defaults + per-butler overrides** — Base model-to-complexity mappings configurable at the system level (dashboard settings page). Each butler can override: enable/disable specific models, add butler-specific model entries, or pin a complexity tier to a specific model.
- **Switchboard complexity classification** — Switchboard gains a new responsibility: before routing, it classifies each inbound request's complexity. This classification is passed through `route_to_butler`.
- **Scheduler complexity hints** — Scheduled tasks gain an optional `complexity` field in `[[butler.schedule]]`. Defaults to `medium` when omitted.
- **Spawner model resolution** — The spawner no longer reads a single model from `butler.toml`. Instead, it resolves the model at spawn time: `(butler_name, complexity, enabled_models)` → `(runtime_type, model_id, extra_args)`.
- **Dashboard UI** — Settings page at `/butlers/settings` for managing the global model catalog. Per-butler model overrides on each butler's config page.
- **BREAKING**: `[butler.runtime].model` in `butler.toml` becomes a fallback-only field. The catalog is the primary source of truth for model selection.

## Capabilities

### New Capabilities
- `model-catalog`: Shared schema table for vendor+model definitions, complexity tiers, and the resolution logic that maps `(butler, complexity)` to a concrete `(runtime, model, args)` tuple. Includes model aliases — named configurations combining a base model with extra runtime args (e.g. `gpt-5.4-high` = `gpt-5.4` + reasoning effort high). Aliases are UI-manageable with seeded defaults for known runtime arg patterns (Codex `model_reasoning_effort`, Claude extended thinking, etc.).
- `complexity-classification`: Switchboard logic for classifying inbound request complexity before routing. Includes the complexity enum definition and the classification interface.
- `dashboard-model-settings`: Dashboard UI and API routes for managing the global model catalog, model aliases (create/edit/delete with seeded examples), complexity-to-model mappings, and per-butler model overrides.

### Modified Capabilities
- `core-spawner`: Model resolution changes from static `config.runtime.model` to dynamic catalog lookup at spawn time.
- `core-scheduler`: Scheduled tasks gain an optional `complexity` field; dispatch passes complexity through to spawner.
- `butler-switchboard`: Route-to-butler flow gains complexity classification step and passes complexity downstream.
- `dashboard-butler-management`: Butler config pages gain per-butler model override UI.

## Impact

- **Database**: New `shared.model_catalog` and `shared.butler_model_overrides` tables. Migration required.
- **Config**: `[butler.runtime].model` becomes fallback; `[butler.runtime].type` and `.args` are superseded by catalog entries but remain for backward compat during rollout.
- **API**: New settings endpoints. `TriggerRequest` may gain optional `complexity` field. Spawner's internal interface changes.
- **Switchboard**: New complexity classification step in the routing flow. May need an LLM call or heuristic-based classifier.
- **All runtimes**: No changes to `RuntimeAdapter` interface — model/args are already parameters. The change is in *who decides* those values.
- **Cost/observability**: Session logs should record which model was selected and why (complexity tier + resolution path) for cost attribution.
