## MODIFIED Requirements

### Requirement: Environment Variables

#### Scenario: Required variables
- **WHEN** the Telegram user client connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=telegram`, `CONNECTOR_CHANNEL=telegram_user_client` must be set
- **AND** `endpoint_identity` is auto-resolved at startup via the Telethon `get_me()` call (e.g., `"telegram:user:<account_id>"`)
- **AND** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_USER_SESSION` must be resolvable

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_BACKFILL_WINDOW_H` (bounded startup replay window), and `CONNECTOR_HEARTBEAT_INTERVAL_S` are optionally configurable

#### Scenario: New default for flush interval
- **WHEN** `TELEGRAM_USER_FLUSH_INTERVAL_S` is not set and no dashboard override exists
- **THEN** the default flush interval is 1800 seconds (30 minutes)

#### Scenario: New default for history time window
- **WHEN** `TELEGRAM_USER_HISTORY_TIME_WINDOW_M` is not set
- **THEN** the default history time window is 35 minutes

## ADDED Requirements

### Requirement: Dashboard Settings Live Reload
The connector SHALL read batch settings from the `connector_registry.settings` JSONB column on each flush scanner cycle, overriding environment variable defaults.

#### Scenario: Dashboard override takes precedence
- **WHEN** the dashboard has set `flush_interval_s` to 900 via the settings API
- **AND** the environment variable `TELEGRAM_USER_FLUSH_INTERVAL_S` is set to 1800
- **THEN** the connector uses 900 as the effective flush interval

#### Scenario: Settings read on flush scanner cycle
- **WHEN** the flush scanner wakes (every 60 seconds)
- **THEN** it reads the cached settings value from the connector registry
- **AND** applies the updated `flush_interval_s` to subsequent buffer age checks

#### Scenario: No dashboard setting falls through to env/default
- **WHEN** no `flush_interval_s` is set in the dashboard settings
- **THEN** the connector uses the environment variable value, or 1800 if unset

### Requirement: Conversation History Payload Type Tag
The connector SHALL tag batch envelopes with `control.payload_type = "conversation_history"` to signal the switchboard pipeline to perform conversation decomposition.

#### Scenario: Payload type tag on batch envelope
- **WHEN** a chat buffer is flushed and the ingest.v1 envelope is assembled
- **THEN** the envelope's `control` section includes `payload_type: "conversation_history"`

#### Scenario: Payload type tag does not affect single-message mode
- **WHEN** the connector operates in single-message mode (if applicable for future use)
- **THEN** the `payload_type` field is not set
