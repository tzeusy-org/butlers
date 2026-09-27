## ADDED Requirements

### Requirement: Steam Credentials in the Secrets Passport

The dashboard SHALL manage Steam account connections from the Steam provider drawer on the Secrets passport (`/secrets`), using `GET`, `POST`, and `DELETE /api/steam/accounts`.

#### Scenario: Connected accounts listed in the drawer

- **WHEN** the owner opens the Steam provider drawer
- **THEN** each connected account SHALL show its display name (or SteamID when unnamed), its SteamID, and a status dot coloured by account status
- **AND** each account SHALL offer a disconnect action

#### Scenario: Disconnect confirms before acting

- **WHEN** the owner chooses disconnect on an account
- **THEN** an inline confirmation SHALL state that syncing stops while the account and its stored API key are retained for reconnection
- **AND** the account SHALL be disconnected only after the owner confirms

#### Scenario: Connect panel

- **WHEN** the owner opens the connect panel
- **THEN** it SHALL offer a masked Steam Web API key input and a SteamID64 input
- **AND** submitting SHALL call `POST /api/steam/accounts`

## MODIFIED Requirements

### Requirement: Steam Connector Configuration

The dashboard API SHALL expose the Steam connector's configuration through `GET` and `PATCH /api/steam/connector/config`, stored in the connector registry's settings rather than in environment variables. There is no dashboard form for these settings.

#### Scenario: Connector configuration fields

- **WHEN** `GET /api/steam/connector/config` is called
- **THEN** the response SHALL carry the effective value of each field — the stored setting when present, otherwise the connector default:
  - `account_rescan_s` (default 300 seconds) — how often the connector checks for new/revoked accounts
  - `heartbeat_interval_s` (default 60 seconds) — how often the connector sends liveness heartbeats
  - `max_tracked_games` (default 10) — maximum games tracked for achievement polling
  - `poll_intervals` per data type with defaults: recently played (300s), online status (300s), achievements (900s), friends (3600s), game library (86400s)
- **AND** a `source` field SHALL read `dashboard` when any stored setting is active and `defaults` otherwise
- **AND** `PATCH` SHALL shallow-merge only the supplied fields, rejecting values outside their bounds (`account_rescan_s` 1–86400, `heartbeat_interval_s` 1–3600, `max_tracked_games` 1–100, poll intervals > 0)
- **AND** the connector SHALL pick up configuration changes on its next rescan cycle without a restart
- **AND** both methods SHALL return 503 when the connector registry is unavailable

#### Scenario: Per-account overrides

- **WHEN** `GET` or `PATCH /api/steam/accounts/{account_id}/config` is called
- **THEN** the API SHALL read or update that account's overrides of poll intervals and tracked games
- **AND** overrides SHALL be stored in the account's `metadata` JSONB column

## REMOVED Requirements

### Requirement: Dashboard UI Components

**Reason**: No `/butlers/settings` Integrations card or gaming-activity widget exists; Steam accounts are managed from the Secrets passport provider drawer.

**Migration**: See "Steam Credentials in the Secrets Passport" in this spec.
