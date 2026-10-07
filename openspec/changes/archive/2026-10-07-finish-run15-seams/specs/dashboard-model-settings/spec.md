## MODIFIED Requirements

### Requirement: Model Catalog Settings API
The dashboard SHALL expose REST endpoints for full CRUD management of the global model catalog with **server-side sort** `(complexity_tier, priority DESC, enabled DESC, alias ASC)`.

The API SHALL accept and round-trip the existing catalog's nullable allowance_account field, validating non-null values as strict strings of 1–64 characters matching the complete lowercase slug [a-z0-9][a-z0-9_-]{0,63}; it SHALL distinguish omitted update fields from explicit null.

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

#### Scenario: Account assignment round-trips through catalog writers
- **WHEN** POST or PUT supplies a valid allowance_account
- **THEN** the actual catalog writer SHALL persist it and list/create/update/full-entry priority responses SHALL expose the same nullable value
- **AND** all existing sort, field, audit and failure contracts SHALL remain intact

#### Scenario: Omitted account retains and explicit null clears
- **WHEN** a PUT omits allowance_account, supplies it as null, or supplies a valid replacement
- **THEN** the old value SHALL respectively remain, become SQL NULL, or become the replacement
- **AND** a null-only allowance_account update SHALL not be discarded as an empty update

#### Scenario: Invalid account label is rejected before mutation
- **WHEN** a non-null allowance_account is not a strict valid slug, including whitespace, a terminal newline, uppercase, email/path, a non-string or more than 64 characters
- **THEN** the API SHALL return 422 before mutation or a success audit
- **AND** omitted/null create SHALL preserve the runtime default without credential inference

### Requirement: Model Catalog Settings UI
The dashboard settings page SHALL include a model catalog management section with full CRUD capabilities and an alias editor.
Models SHALL implement REQ-models-vision-proof-001 through009: declared capability chips, separate latest-check/applied-proof status, explicit bounded Verify confirmation and durable progress, cancellation/reconnect, and distinct Enable/Apply/Refresh CAS actions. There SHALL be no unchecked vision=true editor. Ordinary text verification SHALL remain separate. Missing live authorization disables invocation with a bounded reason; source deployment alone grants none.

ID: REQ-dashboard-model-settings-004
Source: owner-adopted Models vision contract d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; canonical dashboard-model-settings baseline
Scope: v1-mandatory

The existing add/edit catalog form SHALL expose the optional nonsecret allowance-account label with accessible validation and nullable round-trip behavior; it SHALL NOT add an allowance state/countdown page or credential picker.

#### Scenario: Catalog table display
- **WHEN** the settings page loads the model catalog section
- **THEN** a table displays all catalog entries grouped by complexity tier with columns: Alias, Runtime, Model ID, Extra Args (formatted), Tier (badge), Priority, Enabled (toggle), and Actions (Edit, Delete)
- **AND** sections are rendered in the canonical six-tier order [reasoning, workhorse, cheap, specialty, local, legacy]; there is no separate `discretion` group

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

#### Scenario: Models exposes evidence and application separately

- **WHEN** owner inspects a historical true or stale managed row with a fresh unapplied pass
- **THEN** UI shows the declaration and latest pass without claiming applicable managed proof; explicit Apply/Refresh remains required

#### Scenario: Existing editor assigns and clears an account label
- **WHEN** an operator opens the existing Add Model or Edit dialog
- **THEN** an accessible Allowance account input SHALL show the current label or blank and explain that blank uses the runtime default
- **AND** valid input SHALL be saved through the existing catalog mutation; clearing it SHALL send explicit null
- **AND** invalid input SHALL show an inline error and prevent save, while the existing defaults and other form behavior remain

#### Scenario: Catalog refresh preserves account labels
- **WHEN** an operator edits another catalog field, toggles enabled, or steps priority
- **THEN** the account label SHALL retain its current value and remain available in the full-entry response/editor
