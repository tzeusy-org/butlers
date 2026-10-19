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
  - `connector_health`: always null, because the connector records one connector-wide state that is never attributed to a single account

### Requirement: Connector Health View

The dashboard SHALL expose a Steam connector health endpoint that reports the
connector's state from its recorded heartbeat.

#### Scenario: Connector health comes from the recorded heartbeat

- **WHEN** `GET /api/steam/connector/health` is called
- **THEN** the response SHALL report the state from the Steam connector's newest `switchboard.connector_registry` heartbeat row, with `uptime_seconds` and `last_heartbeat_at`
- **AND** the API SHALL make no HTTP call to the connector
- **AND** per-account detail SHALL be empty and `active_accounts` SHALL be null, because the registry records no per-account detail and the account set in a heartbeat is fixed when the connector starts

#### Scenario: Connector not running

- **WHEN** the newest heartbeat is stale, offline, future-dated, or absent
- **THEN** `status` SHALL be `not_running`
- **AND** `last_heartbeat_at` SHALL still report the newest heartbeat when one exists

#### Scenario: Registry unreadable

- **WHEN** the registry cannot be read
- **THEN** `status` SHALL be `unknown` and `registry_available` SHALL be false, rather than `not_running`
