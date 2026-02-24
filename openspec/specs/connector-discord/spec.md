# Discord User Connector (Draft)

## Purpose
The Discord user connector is a **draft-stage** (v2-only, not production-ready) connector that would ingest user-visible Discord messages — DMs and server contexts — into the butler ecosystem. Like the Telegram User Client connector, its purpose is passive contextualization: giving butlers awareness of life events and conversations happening on Discord without requiring manual upload. This connector is ingestion-only and does not define outbound delivery.

## ADDED Requirements

### Requirement: [TARGET-STATE] Discord Connector Scope
Draft connector for user-account-context Discord ingestion.

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

#### Scenario: Environment variables (draft)
- **WHEN** the Discord connector is configured
- **THEN** base connector variables apply plus Discord-specific: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `DISCORD_REFRESH_TOKEN`, and optional `DISCORD_GUILD_ALLOWLIST` and `DISCORD_CHANNEL_ALLOWLIST` for scope controls

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
- **WHEN** the Discord connector is referenced
- **THEN** it is explicitly marked as DRAFT v2-only WIP — not production-ready
- **AND** it does not bypass Switchboard canonical ingest semantics, perform direct routing, or define outbound delivery
