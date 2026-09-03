# Dashboard Model Settings

## Purpose

Provides dashboard REST API endpoints and UI surfaces for managing the global model catalog and per-butler model overrides. Enables operators to configure which models are used at each complexity tier, create model aliases with extra runtime arguments, and override global defaults for specific butlers.

## Requirements

### Requirement: Model Catalog Settings API
The dashboard SHALL expose REST endpoints for full CRUD management of the global model catalog with **server-side sort** `(complexity_tier, priority DESC, enabled DESC, alias ASC)`.

#### Scenario: List catalog entries (server-sorted)
- **WHEN** `GET /api/settings/models` is called
- **THEN** all model catalog entries are returned in the canonical sort order `(complexity_tier ASC under tier order [reasoning, workhorse, cheap, specialty, local, legacy], priority DESC, enabled DESC, alias ASC)`
- **AND** each entry includes `id`, `alias`, `runtime_type`, `model_id`, `extra_args`, `complexity_tier`, `enabled`, `priority`, `session_timeout_s`, `usage_24h`, `usage_30d`, `limit_24h`, `limit_30d`, `last_verified_at`, `last_verified_latency_ms`, `last_verified_ok`, `last_verified_error`, `breaker_open`, `breaker_consecutive_failures`
- **AND** `breaker_open` and `breaker_consecutive_failures` are computed via `model_routing.get_breaker_states` (see `model-catalog` — Dispatch-Outcome Circuit Breaker), batched across the whole list in one additional query, not N+1
- **AND** the frontend MUST NOT re-sort the response; it MAY only filter.

#### Scenario: Create catalog entry
- **WHEN** `POST /api/settings/models` is called with valid fields
- **THEN** a new catalog entry is created and the full entry is returned with its generated `id`
- **AND** required fields are: `alias`, `runtime_type`, `model_id`, `complexity_tier`

#### Scenario: Create with duplicate alias rejected
- **WHEN** `POST /api/settings/models` is called with an alias that already exists
- **THEN** a 409 Conflict response is returned

#### Scenario: Update catalog entry
- **WHEN** `PUT /api/settings/models/{id}` is called with updated fields
- **THEN** the entry is updated atomically and `updated_at` is set to the current time

#### Scenario: Delete catalog entry
- **WHEN** `DELETE /api/settings/models/{id}` is called
- **THEN** the entry is removed from the catalog
- **AND** any butler_model_overrides referencing this entry are cascade-deleted

#### Scenario: Read current catalog delete impact

- **WHEN** `GET /api/settings/models/{id}/delete-impact` is called for an existing entry
- **THEN** the response includes that entry ID and the exact current count of
  `public.butler_model_overrides` rows whose `catalog_entry_id` references it
- **AND** the response contains no override arguments, butler configuration, actor identity, or
  other content-bearing fields
- **AND** an unknown entry returns 404 rather than a fabricated zero

### Requirement: Butler Model Override API
The dashboard SHALL expose REST endpoints for managing per-butler model overrides.

#### Scenario: List overrides for a butler
- **WHEN** `GET /api/butlers/{name}/model-overrides` is called
- **THEN** all overrides for the specified butler are returned, each joined with the referenced catalog entry's alias for display

#### Scenario: Upsert overrides batch
- **WHEN** `PUT /api/butlers/{name}/model-overrides` is called with an array of override objects
- **THEN** each override is upserted (insert or update on conflict of `butler_name + catalog_entry_id`)
- **AND** the full set of overrides for the butler is returned after the operation

#### Scenario: Delete specific override
- **WHEN** `DELETE /api/butlers/{name}/model-overrides/{id}` is called
- **THEN** the override is removed and the butler reverts to global defaults for that catalog entry

### Requirement: Model Catalog Settings UI
The dashboard settings page SHALL include a model catalog management section with full CRUD capabilities and an alias editor.

#### Scenario: Catalog table display
- **WHEN** the settings page loads the model catalog section
- **THEN** a table displays all catalog entries grouped by complexity tier with columns: Alias, Runtime, Model ID, Extra Args (formatted), Tier (badge), Priority, Enabled (toggle), and Actions (Edit, Delete)
- **AND** sections are rendered in the canonical six-tier order [reasoning, workhorse, cheap, specialty, local, legacy]; there is no separate `discretion` group (the discretion vocabulary was retired in migration core_093)

#### Scenario: Create model alias dialog
- **WHEN** the operator clicks "Add Model"
- **THEN** a dialog opens with fields: Alias (text input), Runtime Type (dropdown of registered adapters: `claude`, `codex`, `gemini`, `opencode`), Model ID (text input), Extra Args (key-value editor with "Add arg" button, or raw JSON toggle), Complexity Tier (dropdown: reasoning, workhorse, cheap, specialty, local, legacy), Priority (numeric input, default 0), Enabled (toggle, default true)

#### Scenario: Extra args key-value editor
- **WHEN** the operator edits extra args in key-value mode
- **THEN** each row has a single text input for the CLI token (e.g. `--config` or `model_reasoning_effort=high`)
- **AND** an "Add arg" button appends a new row
- **AND** each row has a remove button
- **AND** a "Raw JSON" toggle switches to a textarea for direct JSON array editing

#### Scenario: Common alias templates
- **WHEN** the operator clicks "Add Model"
- **THEN** a "Use template" dropdown offers pre-configured templates:
  - "Codex with reasoning effort" pre-fills runtime=codex, extra_args=`["--config", "model_reasoning_effort=high"]`
  - "Claude with extended thinking" pre-fills runtime=claude, extra_args appropriate for extended thinking
- **AND** selecting a template populates the form fields, which remain editable

#### Scenario: Edit catalog entry
- **WHEN** the operator clicks "Edit" on a catalog row
- **THEN** the same dialog opens pre-filled with the entry's current values
- **AND** the alias field shows a warning if changed, noting it may affect existing override references

#### Scenario: Delete with dependency check
- **WHEN** the operator clicks "Delete" on a catalog entry
- **THEN** a confirmation dialog fetches the current server-owned delete impact and states the
  exact number of butler overrides that will be cascade-deleted
- **AND** the destructive confirmation stays disabled while that count is loading or unavailable
- **AND** the count is never inferred from client-side catalog data or generic prose

#### Scenario: Toggle enabled inline
- **WHEN** the operator clicks the enabled toggle on a catalog row
- **THEN** the entry's enabled state is toggled immediately via API mutation with a confirmation toast

### Requirement: Per-Butler Model Override UI
Each butler's detail page SHALL include model override configuration in a section accessible from the config or a dedicated tab.

#### Scenario: Override table on butler page
- **WHEN** the operator views a butler's model configuration
- **THEN** a table shows the effective model for each complexity tier: Tier, Effective Model (alias), Source (Global / Override badge), Priority, and an Override action button

#### Scenario: Add override for a tier
- **WHEN** the operator clicks "Override" for a complexity tier
- **THEN** a dialog shows available models for that tier (from global catalog) with options to: select a different model (change priority), disable a model for this butler, or remap a model from another tier

#### Scenario: Clear override
- **WHEN** the operator clicks "Reset to Global" on an override row
- **THEN** the butler-specific override is deleted and the effective model reverts to the global default

### Requirement: Complexity Selection in Trigger UI
The manual trigger UI on each butler's detail page SHALL include a complexity selector.

#### Scenario: Complexity dropdown in trigger tab
- **WHEN** the operator uses the trigger tab to manually spawn a session
- **THEN** a complexity dropdown is shown with the six canonical tier options: Reasoning, Workhorse, Cheap, Specialty, Local, Legacy
- **AND** all six canonical tiers are user-selectable for session triggers
- **AND** the selected complexity is passed to the trigger API

#### Scenario: Resolved model preview
- **WHEN** the operator selects a complexity level
- **THEN** the UI shows which model will be used (e.g. "Will use: claude-sonnet (workhorse tier)") based on the current catalog and overrides

### Requirement: Catalog Priority Stepper API
The dashboard SHALL expose `PUT /api/settings/models/{id}/priority {delta: int}` to adjust a model's priority idempotently.

#### Scenario: Increment priority
- **WHEN** `PUT /api/settings/models/{id}/priority` is called with `{delta: 5}`
- **THEN** the model's `priority` is updated to `max(0, current + 5)`
- **AND** `audit.append("model.priority", target=model_id, note=str(delta))` is invoked
- **AND** the response is the updated catalog entry.

#### Scenario: Priority floor at zero
- **WHEN** a stepper call would push priority below 0
- **THEN** the priority is clamped to 0 (no error).

### Requirement: Catalog Verify-All API
The dashboard SHALL expose `POST /api/settings/models/verify-all` to re-verify every enabled model in parallel. The verification core is shared (`butlers.api.routers.model_settings.run_verify_all_models`) between this manual endpoint and the hourly automated sweep (see Hourly Automated Verification Sweep) so the two can never disagree about what "verified" means or how the result is persisted.

#### Scenario: Verify-all parallel execution
- **WHEN** `POST /api/settings/models/verify-all` is called
- **THEN** the system issues a 1-token completion against each enabled model concurrently with a bounded concurrency of 8
- **AND** for each model, `last_verified_at`, `last_verified_latency_ms`, `last_verified_ok`, and `last_verified_error` are persisted
- **AND** `last_verified_error` is set to the truncated exception text on failure (or `"verification returned an empty response"` when the probe completed with no usable output) and cleared to `NULL` on success
- **AND** the call is rate-limited to once per minute system-wide; subsequent calls within the minute return `429 Too Many Requests`
- **AND** `audit.append("models.verify_all", actor="owner")` is invoked once per accepted run.

### Requirement: Hourly Automated Verification Sweep
The dashboard-api process SHALL run an hourly background sweep
(`butlers.jobs.model_verify.run_model_verify_loop`, started from the FastAPI lifespan
alongside the other periodic jobs) that calls the same verification core as the manual
endpoint, so `last_verified_ok`/`last_verified_at` are never more than roughly one
interval stale even when no operator visits the Models tab.

#### Scenario: Hourly sweep runs independently of the manual rate limit
- **WHEN** the sweep's interval (default `DEFAULT_MODEL_VERIFY_INTERVAL_S = 3600`,
  overridable via `MODEL_VERIFY_INTERVAL_S`) elapses
- **THEN** the sweep calls `run_verify_all_models(pool, audit_actor="model_verify_sweep")`
  directly, bypassing the manual endpoint's once-per-minute HTTP rate limit (that limit is
  an HTTP-surface concern specific to the operator-facing route)
- **AND** `audit.append("models.verify_all", actor="model_verify_sweep")` is invoked,
  distinguishing an automated run from an owner-initiated one in `public.audit_log`

#### Scenario: Sweep sleeps first and tolerates a bad tick
- **WHEN** the dashboard-api process starts
- **THEN** the sweep loop sleeps for one interval before its first run (mirrors
  `run_secrets_lifecycle_loop`), so it never fires real LLM-CLI verification calls during
  a process boot or a test that exercises the full API lifespan
- **AND** a single sweep's failure is logged and swallowed; the loop continues on its
  next interval rather than dying
- **AND** when no shared credential pool is configured, the sweep is a no-op tick (logged
  at WARNING) rather than raising

### Requirement: Catalog Failures Tail API
The dashboard SHALL expose `GET /api/settings/models/{id}/failures?since=24h` returning recent failure entries.

#### Scenario: Failures tail
- **WHEN** `GET /api/settings/models/{id}/failures?since=24h` is called
- **THEN** the response is `PaginatedResponse[FailureEntry]` ordered `ts DESC`
- **AND** each `FailureEntry` includes `ts`, `error_code`, `error_message`, `butler`, `session_id`.

### Requirement: Routing Selection Contract
The runtime SHALL select a model for a butler-requested complexity tier `T` as follows.

#### Scenario: Tier match with multiple candidates
- **WHEN** a butler requests a model in tier `T`
- **THEN** the runtime selects the highest-priority enabled model in `T` whose `last_verified_ok` is `true` or `NULL` (verified or untested; there is no separate `state` column)
- **AND** if no such model exists in `T`, the runtime falls through to the next tier in the canonical order `reasoning → workhorse → cheap → specialty → local → legacy`
- **AND** if no tier yields a candidate, `resolve_model()` returns `None` (no exception is raised; the spawner surfaces the no-eligible-model condition to its caller).

#### Scenario: Disabled models are skipped
- **WHEN** the runtime selects within a tier
- **THEN** models with `enabled = false` MUST NOT be selected even if their priority is highest.

#### Scenario: Models with a failed verification are skipped
- **WHEN** the runtime selects within a tier
- **THEN** models whose `last_verified_ok = false` MUST NOT be selected (verification status is the single boolean `last_verified_ok`; there is no multi-valued `state` column).

### Requirement: Models Page Dispatch Language
The `/settings/models` page SHALL render the catalog in the Dispatch design language with tier-grouped sections.

#### Scenario: Tier-grouped layout
- **WHEN** a user navigates to `/settings/models`
- **THEN** the catalog is rendered as six tier sections in the canonical order
- **AND** each section contains rule-separated rows for its models
- **AND** each row exposes: model name, role, priority stepper (↑/↓), enable toggle, `Test →`, `Edit →`, `Delete →`
- **AND** filter chips (tier, state) constrain the visible rows but do not re-order them.

#### Scenario: Empty tier
- **WHEN** a tier section has no models
- **THEN** the section renders a single serif-italic line "Nothing in this tier." and no rows
- **AND** the section header remains visible (do not hide the eyebrow).

#### Scenario: Verification age, stored error, and routing consequence pixels
- **WHEN** a model row renders its verification badge
- **THEN** the row shows the verification age next to the ✓/✗ mark — a relative-compact
  timestamp (`<Time mode="relative-compact">`) when `last_verified_at` is set, or the
  literal text "never verified" when it is `null` — so staleness is never silently implied
  by an ageless checkmark
- **AND** when `last_verified_ok = false`, the ✗ mark's tooltip (`title` attribute) shows
  the stored `last_verified_error` text (or a generic fallback when absent)
- **AND** when `breaker_open = true`, the row shows a "breaker" badge whose tooltip states
  the routing consequence and the `breaker_consecutive_failures` count — this is a
  distinct signal from verification staleness (a breaker-open entry may still show
  `last_verified_ok = true` if it was last manually verified before the failures began)

#### Scenario: Priority stepper round-trip
- **WHEN** a user clicks the up or down stepper on a model row
- **THEN** the page calls `PUT /api/settings/models/{id}/priority` and re-fetches the list
- **AND** in dev, the visible round-trip MUST complete within 200ms.

## Source References
- PLAN.md §2 settled decisions (six-tier catalog, sort contract, routing contract) and §5 `/settings/models` API.
- Visual reference: the `ModelCatalogExpanded` redesign prototype (graduated; now shipped in `frontend/`).
- Reuses `audit.append()` from dashboard-audit-log on every mutation.
