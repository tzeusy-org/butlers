## MODIFIED Requirements

### Requirement: Environment Variables
The connector SHALL take operational configuration from base and Telegram-specific environment variables, and static identity from its canonical descriptor.

ID: REQ-connector-telegram-bot-004
Source: bu-dss7he.9 released S3; openspec/specs/connector-telegram-bot/spec.md requirement Environment Variables
Scope: v1-mandatory

#### Scenario: Required variables
- **WHEN** the Telegram bot connector starts
- **THEN** `SWITCHBOARD_MCP_URL` must be set; the canonical descriptor SHALL supply provider `telegram` and channel `telegram_bot`, independently of identity environment variables
- **AND** `endpoint_identity` is auto-resolved at startup via the Telegram Bot API `getMe()` call (e.g., `"telegram:bot:<bot_username>"`)
- **AND** `CONNECTOR_POLL_INTERVAL_S` is required for polling mode

#### Scenario: Telegram credential variables
- **WHEN** the bot scope is enabled
- **THEN** `BUTLER_TELEGRAM_TOKEN` (or the custom name from `modules.telegram.bot.token_env`) must be resolvable

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40081), and `CONNECTOR_WEBHOOK_URL` (enables webhook mode) are optionally configurable
