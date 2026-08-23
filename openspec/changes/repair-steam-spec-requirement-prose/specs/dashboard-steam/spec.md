## MODIFIED Requirements

### Requirement: Account Status Endpoint

The dashboard SHALL expose a per-account Steam status endpoint reporting
credential presence, key validity, last poll time, and connector health.

#### Scenario: Per-account status

- **WHEN** `GET /api/steam/accounts/<id>/status` is called
- **THEN** the response SHALL include:
  - `has_api_key`: boolean (entity_info exists with type `steam_api_key`)
  - `key_valid`: boolean (result of a test API call)
  - `last_poll_at`: timestamp or null
  - `connector_health`: health status for this account from the connector (if running)

### Requirement: Connector Health View

The dashboard SHALL expose a Steam connector health endpoint that proxies the
connector's own health endpoint.

#### Scenario: Get connector health

- **WHEN** `GET /api/steam/connector/health` is called
- **THEN** the response SHALL proxy the Steam connector's health endpoint
- **AND** return aggregated and per-account health status

### Requirement: Dashboard UI Components

The dashboard SHALL present Steam connection management in the settings
Integrations section, and MAY surface gaming activity on relevant domain
pages.

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
