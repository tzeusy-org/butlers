## MODIFIED Requirements

### Requirement: Model Catalog Settings UI
The dashboard settings page SHALL include a model catalog management section with full CRUD capabilities and an alias editor.

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
