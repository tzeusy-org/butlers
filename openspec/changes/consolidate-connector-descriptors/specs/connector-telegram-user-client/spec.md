## MODIFIED Requirements

### Requirement: Environment Variables
The connector SHALL support the required and optional operational environment-variable configuration described below, and derive static identity from its canonical descriptor.

ID: REQ-connector-telegram-user-client-001
Source: bu-dss7he.9 released S3; openspec/specs/connector-telegram-user-client/spec.md requirement Environment Variables
Scope: v1-mandatory

#### Scenario: Required variables
- **WHEN** the Telegram user client connector starts
- **THEN** `SWITCHBOARD_MCP_URL` must be set; the canonical descriptor SHALL supply provider `telegram` and channel `telegram_user_client`, independently of identity environment variables
- **AND** `endpoint_identity` is auto-resolved at startup via the Telethon `get_me()` call (e.g., `"telegram:user:<account_id>"`)
- **AND** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_USER_SESSION` must be present on the owner entity's `public.entity_info` (resolved from the database, not read from environment variables)

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40080), `CONNECTOR_BACKFILL_WINDOW_H` (bounded startup replay window), and `CONNECTOR_HEARTBEAT_INTERVAL_S` are optionally configurable

#### Scenario: New default for flush interval
- **WHEN** `TELEGRAM_USER_FLUSH_INTERVAL_S` is not set and no dashboard override exists
- **THEN** the default flush interval is 1800 seconds (30 minutes)

#### Scenario: New default for history time window
- **WHEN** `TELEGRAM_USER_HISTORY_TIME_WINDOW_M` is not set
- **THEN** the default history time window is 35 minutes
