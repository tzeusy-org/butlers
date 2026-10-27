## MODIFIED Requirements

### Requirement: Core groups editor supports array input

The `core_groups` field SHALL be editable as a multi-select or tag input from the known group names.

Source: RFC 0002 §Core Tools

#### Scenario: Add a core group
- **WHEN** the user adds a group to core_groups from the known list (infra, state, scheduling, sessions, notifications, media, graph, temporal, module_mgmt, switchboard_routing, switchboard_backfill, delegation, domain_events, fleet_cases)
- **THEN** the group SHALL appear in the list and be included in the PATCH payload on save

#### Scenario: Remove a core group
- **WHEN** the user removes a group from core_groups
- **THEN** the group SHALL be excluded from the PATCH payload on save
- **AND** the UI SHALL require a non-blank narrowing reason before saving

#### Scenario: Unknown groups cannot be added via UI
- **WHEN** the user interacts with the core_groups editor
- **THEN** only known group names SHALL be selectable (free-text input of arbitrary group names is not allowed)
