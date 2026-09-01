## MODIFIED Requirements

### Requirement: Metadata Schema

The implementation SHALL provide the behavior described by this requirement.
The `metadata` JSONB column stores per-account configuration overrides.

#### Scenario: Default metadata structure

- **WHEN** a Steam account is created with no metadata overrides
- **THEN** `metadata` SHALL default to `{}`
- **AND** the connector SHALL use global defaults for all poll intervals and settings

#### Scenario: Per-account poll interval overrides

- **WHEN** `metadata` contains `{"poll_intervals": {"recently_played": 300, "achievements": 900}}`
- **THEN** the connector SHALL use those intervals for this account instead of global defaults
- **AND** data types not listed SHALL use global defaults

#### Scenario: Tracked games override

- **WHEN** `metadata` contains `{"tracked_games": [730, 570, 440]}`
- **THEN** the connector SHALL track achievements only for those app IDs instead of auto-detecting from recently played
