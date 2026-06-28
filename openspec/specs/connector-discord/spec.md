# Discord Connector

## STATUS: Bot-Token Gateway Connector Shipped; User-Context OAuth Flow is TARGET-STATE (v2)

**The shipped reference implementation (`src/butlers/connectors/discord_user.py`) is a functional bot-token Discord Gateway client. The broader user-account-context OAuth user-flow described later in this spec is TARGET-STATE (v2) and is not yet implemented.**

This spec covers two distinct layers, and they must not be confused:

- **Currently shipped (as-built):** a Discord bot-token Gateway connector. It authenticates with a Discord bot token (`Authorization: Bot <token>`, `discord_user.py:479`), connects to the Discord Gateway over WebSocket, normalizes message events to `ingest.v1`, and submits them to Switchboard. Configuration resolves `DISCORD_BOT_TOKEN` from env or the DB credential store (`discord_user.py:42-51,216`) plus optional guild/channel allowlists. The runtime loop lives in `start()` (`discord_user.py:458-635`).
- **Target-state (v2, not built):** a full user-account-context ingestion model authenticated by an OAuth user-flow (`DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `DISCORD_REFRESH_TOKEN`). This remains a draft, gated behind platform Terms of Service review, consent and scope-disclosure UI, and the remaining items listed below.

Still-incomplete components for the v2 target-state:

- User-context OAuth user-flow finalization (the shipped connector uses a bot token instead)
- Scope validation and least-privilege defaults
- User revocation and connector shutdown behavior
- Retention and redaction policy implementation
- Platform Terms of Service alignment review
- Explicit user consent and scope disclosure UI
- Full error recovery and retry logic
- Production-grade testing and monitoring

> **OPEN OWNER DECISION (auth model):** The intended long-term Discord auth model is not settled. The connector ships today as a bot-token Gateway client, while this spec's v2 target describes an OAuth user-flow for user-account-context ingestion. Which model is canonical for Discord (bot-token gateway vs OAuth user-flow) is an open owner decision and is deliberately left unresolved here. This spec does not pick a winner; it only records what is shipped versus what is targeted.

**Decision:** Rather than presenting the OAuth user-flow as the shipped contract, this spec documents the as-built bot-token Gateway connector as current and parks the OAuth user-flow as v2 target-state. Future work should resolve the auth-model decision above and validate Discord ToS alignment and user privacy requirements before pursuing the user-context implementation.

## Purpose
The Discord connector ingests Discord message events into the butler ecosystem for passive contextualization, giving butlers awareness of conversations happening on Discord without requiring manual upload. The currently-shipped connector is a bot-token Gateway client (see STATUS above); a broader user-account-context model (DMs and user-visible server contexts), authenticated by an OAuth user-flow, is a v2 target-state described later in this spec. This connector is ingestion-only and does not define outbound delivery.

## ADDED Requirements

### Requirement: [AS-BUILT] Shipped Bot-Token Gateway Connector
The currently-shipped Discord connector authenticates with a Discord bot token and ingests events over the Discord Gateway. This is the as-built behavior reflected in `src/butlers/connectors/discord_user.py`, and it is the spec's described-current state for Discord.

#### Scenario: Bot-token Gateway authentication (current)
- **WHEN** the shipped Discord connector starts
- **THEN** it authenticates to Discord using a bot token via the `Authorization: Bot <token>` HTTP header (`discord_user.py:479`)
- **AND** the bot token is resolved from `DISCORD_BOT_TOKEN` (env fallback) or the DB credential store (`discord_user.py:42-51,216`)
- **AND** it connects to the Discord Gateway over WebSocket and runs the identify/resume handshake in `start()` (`discord_user.py:458-635`)
- **AND** it does NOT use an OAuth user-flow today (none of `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `DISCORD_REFRESH_TOKEN` is required to run)

#### Scenario: Current configuration variables
- **WHEN** the shipped connector is configured
- **THEN** base connector variables apply plus `DISCORD_BOT_TOKEN` (required), and optional `DISCORD_GUILD_ALLOWLIST` and `DISCORD_CHANNEL_ALLOWLIST` for scope control
- **AND** the OAuth user-flow variables are NOT part of the shipped configuration (see the v2 target-state below)

#### Scenario: Current ingestion behavior
- **WHEN** the shipped connector receives a Discord Gateway message event
- **THEN** it normalizes the event to `ingest.v1` and submits it to Switchboard
- **AND** it maintains a durable per-channel checkpoint for idempotent replay on restart

### Requirement: [TARGET-STATE] Discord Connector Scope
Draft v2 connector layer for user-account-context Discord ingestion, distinct from the shipped bot-token Gateway connector above.

#### Scenario: Target v2 scope
- **WHEN** the Discord user connector is implemented
- **THEN** it supports live ingestion of user-visible Discord messages and relevant edits/deletes
- **AND** it supports DM and server contexts that the linked user account is authorized to see
- **AND** it supports optional bounded historical backfill for startup recovery

#### Scenario: ingest.v1 field mapping
- **WHEN** a Discord message is normalized
- **THEN** the mapping is:
  - `source.channel` = `"discord"`
  - `source.provider` = `"discord"`
  - `source.endpoint_identity` = `"discord:user:<user_id>"`
  - `event.external_event_id` = Discord message/event ID (Snowflake)
  - `event.external_thread_id` = channel/thread/conversation ID
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = Discord author ID
  - `payload.raw` = full Discord payload
  - `payload.normalized_text` = extracted message text

#### Scenario: Environment variables (v2 target-state, OAuth user-flow)
- **WHEN** the v2 user-context Discord connector is implemented
- **THEN** base connector variables apply plus Discord-specific OAuth user-flow variables: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `DISCORD_REFRESH_TOKEN`, and optional `DISCORD_GUILD_ALLOWLIST` and `DISCORD_CHANNEL_ALLOWLIST` for scope controls
- **AND** these OAuth variables are reserved for future implementation and are NOT consumed by the currently-shipped bot-token Gateway connector (which uses `DISCORD_BOT_TOKEN`)

#### Scenario: Live ingestion model
- **WHEN** the Discord connector runs
- **THEN** it maintains a live Discord event stream (gateway/streaming model)
- **AND** normalizes each event to `ingest.v1` and submits immediately to Switchboard
- **AND** uses durable checkpoint with idempotent replay on restart

#### Scenario: Privacy and compliance (pending)
- **WHEN** evaluating the Discord connector for production
- **THEN** finalization is required for: auth pattern and platform ToS alignment, approved scopes and least-privilege defaults, user revocation and connector shutdown behavior, retention/redaction policy defaults
- **AND** explicit user consent and scope disclosure are mandatory before enabling

#### Scenario: Draft status
- **WHEN** the v2 user-context Discord connector layer is referenced
- **THEN** it is explicitly marked as DRAFT v2-only WIP, not production-ready (distinct from the shipped bot-token Gateway connector documented above)
- **AND** it does not bypass Switchboard canonical ingest semantics, perform direct routing, or define outbound delivery
