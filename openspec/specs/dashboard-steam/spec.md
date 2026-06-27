# Dashboard Steam

## Purpose

Provides dashboard REST API endpoints and UI components for managing connected Steam accounts and viewing gaming activity. Includes account lifecycle management (connect, disconnect, set primary, status), a playtime analytics API backed by `connectors.steam_play_history`, and a connector health status view.

## Requirements

### Requirement: Steam Accounts Management API

The dashboard SHALL expose REST endpoints for managing connected Steam accounts, following the Google Accounts management pattern.

#### Scenario: List connected accounts

- **WHEN** `GET /api/steam/accounts` is called
- **THEN** the response SHALL return an array of account objects with: `id`, `steam_id`, `display_name`, `profile_url`, `avatar_url`, `is_primary`, `status`, `connected_at`, `last_poll_at`
- **AND** accounts SHALL be ordered by `is_primary DESC, connected_at ASC`
- **AND** no credential material (API keys) SHALL be included

#### Scenario: Connect a new Steam account

- **WHEN** `POST /api/steam/accounts` is called with `{"steam_id": "76561198000000000", "api_key": "..."}`
- **THEN** the backend SHALL validate the API key by calling `ISteamUser/GetPlayerSummaries/v2` with the provided key and SteamID
- **AND** on success, create the account row, companion entity, and entity_info per the steam-account-registry spec
- **AND** populate `display_name`, `profile_url`, `avatar_url` from the validation response
- **AND** return the created account object (without API key)
- **AND** on validation failure, return an error status (`400` for a 401/403 key rejection; `429`/`502` for other upstream failures) with FastAPI's standard `{"detail": "<message>"}` envelope and failure category `invalid_api_key`

#### Scenario: Set primary account

- **WHEN** `PUT /api/steam/accounts/<id>/primary` is called
- **THEN** the specified account SHALL become primary and all others SHALL have `is_primary = false`
- **AND** the response SHALL return the updated account object

#### Scenario: Disconnect account

- **WHEN** `DELETE /api/steam/accounts/<id>` is called
- **THEN** the account's `status` SHALL be set to `'revoked'`
- **AND** the response SHALL confirm the disconnection

#### Scenario: Hard delete

- **WHEN** `DELETE /api/steam/accounts/<id>?hard_delete=true` is called
- **THEN** the `steam_accounts` row and companion entity SHALL be fully deleted

### Requirement: Account Status Endpoint

#### Scenario: Per-account status

- **WHEN** `GET /api/steam/accounts/<id>/status` is called
- **THEN** the response SHALL include:
  - `has_api_key`: boolean (entity_info exists with type `steam_api_key`)
  - `key_valid`: boolean (result of a test API call)
  - `last_poll_at`: timestamp or null
  - `connector_health`: health status for this account from the connector (if running)

### Requirement: Play History Storage

The connector SHALL persist per-game daily playtime aggregates in `connectors.steam_play_history` for dashboard analytics and butler queries.

#### Schema

```sql
CREATE TABLE connectors.steam_play_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    steam_account_id UUID NOT NULL REFERENCES public.steam_accounts(id) ON DELETE CASCADE,
    app_id INTEGER NOT NULL,
    app_name VARCHAR NOT NULL,
    date DATE NOT NULL,
    playtime_minutes INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_steam_play_history UNIQUE (steam_account_id, app_id, date)
);
```

Indexes:
- `ix_steam_play_history_steam_account_id` on `(steam_account_id)` (supports per-account lookup)
- `ix_steam_play_history_app_id` on `(app_id)` (supports per-game queries)

#### Scenario: Connector writes daily playtime

- **WHEN** the recently-played poller detects a playtime delta for a game
- **THEN** it SHALL upsert into `connectors.steam_play_history` with the current date, adding the playtime delta to any existing row for the same `(steam_account_id, app_id, date)`
- **AND** `app_name` SHALL be populated from the API response for human-readable queries

#### Scenario: Baseline does not backfill

- **WHEN** the recently-played poller runs for the first time on an account
- **THEN** it SHALL NOT write historical playtime to `steam_play_history` (only forward deltas from this point)

### Requirement: Playtime Analytics API

The dashboard SHALL expose endpoints for querying playtime data.

#### Scenario: Get playtime summary for a period

- **WHEN** `GET /api/steam/playtime?days=30` is called (default 30, max 3650)
- **THEN** the response SHALL return:
  - `total_minutes`: total playtime across all games in the period
  - `games`: array of `{app_id, app_name, total_minutes}` sorted by total_minutes DESC
  - `daily`: array of `{date, total_minutes}` for the period

#### Scenario: Get playtime for a specific game

- **WHEN** `GET /api/steam/playtime/<app_id>?days=30` is called
- **THEN** the response SHALL return:
  - `app_id`, `app_name`
  - `total_minutes`: total for the period
  - `history`: array of `{date, playtime_minutes, recorded_at}` for the period

#### Scenario: Playtime scoped to primary account by default

- **WHEN** playtime endpoints are called without `account_id` parameter
- **THEN** they SHALL use the primary Steam account
- **AND** if `account_id` query param is provided, use that account instead

### Requirement: Connector Health View

#### Scenario: Get connector health

- **WHEN** `GET /api/steam/connector/health` is called
- **THEN** the response SHALL proxy the Steam connector's health endpoint
- **AND** return aggregated and per-account health status

### Requirement: Steam Connector Configuration

The dashboard SHALL provide a configuration section for Steam connector settings at `/butlers/settings` under the Steam card. No environment variables — all configuration is managed through the dashboard.

#### Scenario: Connector configuration fields

- **WHEN** a Steam account is connected and the user expands the Steam settings section
- **THEN** the dashboard SHALL display configurable fields:
  - **Account rescan interval** (default 300 seconds) — how often the connector checks for new/revoked accounts
  - **Heartbeat interval** (default 60 seconds) — how often the connector sends liveness heartbeats
  - **Max tracked games** (default 10) — maximum games tracked for achievement polling
  - **Poll intervals** per data type with defaults: recently played (300s), online status (300s), achievements (900s), friends (3600s), game library (86400s)
- **AND** changes SHALL be persisted to the connector's configuration store (not environment variables)
- **AND** the connector SHALL pick up configuration changes on the next rescan cycle

#### Scenario: Per-account overrides

- **WHEN** the user clicks "Configure" on a specific Steam account
- **THEN** the dashboard SHALL allow overriding poll intervals and tracked games for that account
- **AND** overrides SHALL be stored in the account's `metadata` JSONB column

### Requirement: Dashboard UI Components

#### Scenario: Steam integration card on settings page

- **WHEN** the user navigates to `/butlers/settings`
- **THEN** a "Steam" card SHALL appear in the Integrations section
- **AND** it SHALL show connection status, connected accounts, and a "Connect Steam Account" button
- **AND** connected accounts SHALL show avatar, display name, SteamID, primary badge, and disconnect button

#### Scenario: Connect form

- **WHEN** the user clicks "Connect Steam Account"
- **THEN** a form SHALL appear with:
  - Link to `https://steamcommunity.com/dev/apikey` with instructions to register a key
  - SteamID input field (with link to SteamID lookup tools)
  - API Key input field (masked)
  - "Validate & Connect" button

#### Scenario: Activity overview on domain page

- **WHEN** a Steam account is connected and playtime data exists
- **THEN** the dashboard MAY show a gaming activity widget on relevant domain pages (e.g., general, lifestyle)
- **AND** the widget SHALL display: recent games played, hours this week, and a simple daily playtime chart
