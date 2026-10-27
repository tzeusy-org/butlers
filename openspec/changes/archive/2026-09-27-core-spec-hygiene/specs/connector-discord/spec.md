## MODIFIED Requirements

### Requirement: [AS-BUILT] Shipped Bot-Token Gateway Connector
The currently-shipped Discord connector SHALL authenticate with a Discord bot token and ingest events over the Discord Gateway. This is the as-built behavior reflected in `src/butlers/connectors/discord_user.py`, and it is the spec's described-current state for Discord.

#### Scenario: Bot-token Gateway authentication (current)
- **WHEN** the shipped Discord connector starts
- **THEN** it authenticates to Discord using a bot token via the `Authorization: Bot <token>` HTTP header
- **AND** the bot token is resolved from `DISCORD_BOT_TOKEN` (env fallback) or the DB credential store
- **AND** it connects to the Discord Gateway over WebSocket and runs the identify/resume handshake
- **AND** it does NOT use an OAuth user-flow today (none of `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `DISCORD_REFRESH_TOKEN` is required to run)

#### Scenario: Current configuration variables
- **WHEN** the shipped connector is configured
- **THEN** base connector variables apply plus `DISCORD_BOT_TOKEN` (required), and optional `DISCORD_GUILD_ALLOWLIST` and `DISCORD_CHANNEL_ALLOWLIST` for scope control
- **AND** the OAuth user-flow variables are NOT part of the shipped configuration (see the v2 target-state below)

#### Scenario: Current ingestion behavior
- **WHEN** the shipped connector receives a Discord Gateway message event
- **THEN** it normalizes the event to `ingest.v1` and submits it to Switchboard
- **AND** it maintains a durable per-channel checkpoint for idempotent replay on restart
