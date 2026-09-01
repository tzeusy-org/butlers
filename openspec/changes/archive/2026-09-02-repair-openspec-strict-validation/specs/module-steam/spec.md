## MODIFIED Requirements

### Requirement: Steam Module Configuration

The implementation SHALL provide the behavior described by this requirement.
The module is configured via `[modules.steam]` in `butler.toml`.

#### Scenario: Config structure

- **WHEN** `[modules.steam]` is configured
- **THEN** it SHALL accept:
  - `default_account` (optional, UUID or SteamID) — override which Steam account to use as default instead of primary
  - `cache_ttl_seconds` (default 300) — TTL for cached API responses to avoid redundant calls within a session
  - `max_batch_size` (default 100) — max SteamIDs per batch in `GetPlayerSummaries`

#### Scenario: Module name and dependencies

- **WHEN** the Steam module is registered
- **THEN** `module.name` SHALL be `"steam"`
- **AND** `module.dependencies` SHALL be `[]` (no module dependencies)

### Requirement: Credential Resolution

The implementation SHALL provide the behavior described by this requirement.
The module resolves Steam API keys at startup from the account registry.

#### Scenario: Startup credential resolution

- **WHEN** `on_startup` is called
- **THEN** the module SHALL query `public.steam_accounts` for the primary account (or `default_account` if configured)
- **AND** resolve the API key from the companion entity's `entity_info` where `type = 'steam_api_key'`
- **AND** cache the resolved key and SteamID for tool use

#### Scenario: No Steam account configured

- **WHEN** `on_startup` is called and no active Steam account exists
- **THEN** the module SHALL log a warning and start in degraded mode
- **AND** all tools SHALL return an actionable error: `{"error": "no_steam_account", "message": "No Steam account is connected.", "hint": "Connect a Steam account via the dashboard settings and set it as primary."}`
