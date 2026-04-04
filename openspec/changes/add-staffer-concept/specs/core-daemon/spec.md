## ADDED Requirements

### Requirement: Agent Type Awareness
The daemon SHALL read the `type` field from `butler.toml` config and apply type-specific behaviors at well-defined decision points. The daemon engine remains unified — there is no separate `StafferDaemon` class.

#### Scenario: Config parsing includes type
- **WHEN** the daemon loads `butler.toml`
- **THEN** it parses `[butler].type` as a `ButlerType` enum (`BUTLER` or `STAFFER`)
- **AND** defaults to `ButlerType.BUTLER` if the field is absent
- **AND** the `ButlerConfig` dataclass exposes `config.type` for downstream decision points

#### Scenario: Config parsing includes permissions
- **WHEN** the daemon loads `butler.toml` with a `[butler.permissions]` section
- **THEN** it parses `cross_butler_access` as a list of strings
- **AND** defaults to an empty list if the section or field is absent
- **AND** the `ButlerConfig` dataclass exposes `config.permissions.cross_butler_access`

#### Scenario: Staffer-specific startup behaviors
- **WHEN** the daemon starts with `config.type == ButlerType.STAFFER`
- **THEN** it proceeds through the same lifecycle phases as a butler
- **AND** during schedule sync, it skips registration of any `daily_briefing_contribution` schedule entries
- **AND** during switchboard registration, it includes `type = "staffer"` in the registration payload so the switchboard can exclude it from user-message routing

#### Scenario: Butler-specific startup behaviors unchanged
- **WHEN** the daemon starts with `config.type == ButlerType.BUTLER`
- **THEN** startup proceeds exactly as before this change — no behavioral differences from the pre-staffer codebase
