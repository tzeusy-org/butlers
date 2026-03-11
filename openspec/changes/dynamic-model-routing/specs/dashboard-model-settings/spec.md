## ADDED Requirements

### Requirement: Model Catalog Settings API
The dashboard SHALL expose REST endpoints for full CRUD management of the global model catalog.

#### Scenario: List catalog entries
- **WHEN** `GET /api/settings/models` is called
- **THEN** all model catalog entries are returned ordered by `complexity_tier` then `priority` then `alias`
- **AND** each entry includes all fields: `id`, `alias`, `runtime_type`, `model_id`, `extra_args`, `complexity_tier`, `enabled`, `priority`, `created_at`, `updated_at`

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

#### Scenario: Create model alias dialog
- **WHEN** the operator clicks "Add Model"
- **THEN** a dialog opens with fields: Alias (text input), Runtime Type (dropdown of registered adapters: `claude-code`, `codex`, `gemini`, `opencode`), Model ID (text input), Extra Args (key-value editor with "Add arg" button, or raw JSON toggle), Complexity Tier (dropdown: trivial, medium, high, extra_high), Priority (numeric input, default 0), Enabled (toggle, default true)

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
  - "Claude with extended thinking" pre-fills runtime=claude-code, extra_args appropriate for extended thinking
- **AND** selecting a template populates the form fields, which remain editable

#### Scenario: Edit catalog entry
- **WHEN** the operator clicks "Edit" on a catalog row
- **THEN** the same dialog opens pre-filled with the entry's current values
- **AND** the alias field shows a warning if changed, noting it may affect existing override references

#### Scenario: Delete with dependency check
- **WHEN** the operator clicks "Delete" on a catalog entry
- **THEN** a confirmation dialog warns if butler overrides reference this entry
- **AND** confirms that those overrides will be cascade-deleted

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
- **THEN** a complexity dropdown is shown with options: Trivial, Medium (default), High, Extra High
- **AND** the selected complexity is passed to the trigger API

#### Scenario: Resolved model preview
- **WHEN** the operator selects a complexity level
- **THEN** the UI shows which model will be used (e.g. "Will use: claude-sonnet (medium tier)") based on the current catalog and overrides
