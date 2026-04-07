## ADDED Requirements

### Requirement: Config tab displays runtime config from DB

The butler detail config tab SHALL display the effective runtime config read from the `runtime-config` API endpoint, not from the raw toml.

Source: RFC 0007 §Dashboard API Surface
Scope: v1-mandatory

#### Scenario: Config tab shows DB values
- **WHEN** the user opens the config tab for a butler
- **THEN** the tab SHALL show current runtime config values from the DB with editable fields

#### Scenario: Cold fields show restart badge
- **WHEN** a cold field (core_groups, max_concurrent, max_queued) is displayed
- **THEN** the field SHALL show a visual indicator that changes require a daemon restart

### Requirement: Config tab supports inline editing

The config tab SHALL allow the user to edit runtime config fields and save via the PATCH endpoint.

Source: RFC 0007 §Dashboard API Surface
Scope: v1-mandatory

#### Scenario: Edit and save a field
- **WHEN** the user edits a field value and clicks save
- **THEN** the PATCH endpoint SHALL be called and the UI SHALL reflect the updated value

#### Scenario: Restart-required feedback after saving cold field
- **WHEN** the user saves a change that includes cold fields
- **THEN** the UI SHALL display a notification listing which fields require a daemon restart to take effect

### Requirement: Core groups editor supports array input

The `core_groups` field SHALL be editable as a multi-select or tag input from the known group names.

Source: RFC 0002 §Core Tools
Scope: v1-mandatory

#### Scenario: Add a core group
- **WHEN** the user adds a group to core_groups from the known list (infra, state, scheduling, sessions, notifications, media, temporal, module_mgmt, switchboard_routing, switchboard_backfill)
- **THEN** the group SHALL appear in the list and be included in the PATCH payload on save

#### Scenario: Remove a core group
- **WHEN** the user removes a group from core_groups
- **THEN** the group SHALL be excluded from the PATCH payload on save

#### Scenario: Unknown groups cannot be added via UI
- **WHEN** the user interacts with the core_groups editor
- **THEN** only known group names SHALL be selectable (free-text input of arbitrary group names is not allowed)
