# Telegram User Client Connector

## Purpose
The Telegram User Client connector provides **readonly access to the user's personal Telegram account** — not the butler's bot account. Its sole purpose is contextualization: by ingesting the user's DMs, group chats, supergroups, and channels, butlers gain awareness of life events, commitments, relationships, and facts flowing through Telegram without requiring explicit manual upload. The connector reads messages the user can see; it never sends, replies, or modifies anything on the user's Telegram account. All outbound messaging goes through the separate Telegram Bot connector and Messenger butler.

## ADDED Requirements

### Requirement: Readonly Contextualization Role
The user client connector exists to give butlers passive awareness of the user's Telegram activity. It is strictly ingestion-only and readonly.

#### Scenario: Readonly access to user's Telegram
- **WHEN** the Telegram user client connector runs
- **THEN** it ingests messages visible to the user's personal Telegram account (not the butler bot)
- **AND** it never sends messages, replies, reacts, edits, deletes, or modifies anything on the user's account
- **AND** all ingested messages flow through Switchboard for classification and routing to specialist butlers for contextual awareness

#### Scenario: Contextualization use cases
- **WHEN** the user's Telegram messages are ingested
- **THEN** butlers can automatically learn about: travel plans mentioned in group chats, restaurant recommendations from friends, health appointments shared in DMs, financial discussions, relationship events, and any other life context visible in Telegram
- **AND** this eliminates the need for the user to manually forward messages or tell the butler about events already visible in their Telegram

#### Scenario: Separation from bot connector
- **WHEN** both Telegram connectors run simultaneously
- **THEN** the bot connector handles user↔bot interactive messaging (inbound + outbound via Messenger)
- **AND** the user client connector provides passive, readonly ingestion of the user's broader Telegram activity
- **AND** each has a distinct `endpoint_identity` (e.g., `"telegram:bot:mybot"` vs `"telegram:user:123456"`)

### Requirement: Live-Stream First Ingestion
The user client connector maintains a persistent Telegram session for near-real-time message ingestion.

#### Scenario: Live event subscription
- **WHEN** the connector starts
- **THEN** it connects to Telegram via Telethon `StringSession` with `api_id` and `api_hash`
- **AND** registers a live `NewMessage` event handler
- **AND** runs until disconnected, immediately normalizing and submitting each message to Switchboard

#### Scenario: Telethon MTProto transport
- **WHEN** the connector connects to Telegram
- **THEN** it uses Telethon's MTProto protocol (the same protocol used by official Telegram desktop/mobile clients)
- **AND** this gives access to all messages visible to the user account — not limited to bot-addressed messages like the Bot API

#### Scenario: No polling mode
- **WHEN** the user client connector operates
- **THEN** it is live-stream only (no periodic polling like the bot connector)
- **AND** on disconnect, it reconnects with jittered backoff and replays from checkpoint

#### Scenario: Telethon optional dependency
- **WHEN** Telethon is not installed
- **THEN** the connector raises `RuntimeError` at startup with a clear message
- **AND** the `TELETHON_AVAILABLE` flag allows conditional import without crashing the module

### Requirement: Scope of Ingestion
The connector ingests from all message sources visible to the user's Telegram account.

#### Scenario: Ingested message sources
- **WHEN** the user client connector is running
- **THEN** it may ingest from: direct messages, group chats, supergroups, channels, and threaded discussions visible to the account

#### Scenario: Message ordering
- **WHEN** messages arrive
- **THEN** per-dialog message ordering is preserved where practical
- **AND** cross-dialog global ordering is not guaranteed

#### Scenario: Inbound and outbound messages
- **WHEN** the user sends or receives a message on Telegram
- **THEN** both inbound (messages from others) and outbound (messages the user sent) are ingested
- **AND** this gives butlers full conversational context, not just one side

### Requirement: ingest.v1 Field Mapping
Each user-client message is normalized to the canonical `ingest.v1` envelope.

#### Scenario: Field mapping
- **WHEN** a user-client message is normalized
- **THEN** the mapping is:
  - `source.channel` = `"telegram"`
  - `source.provider` = `"telegram"`
  - `source.endpoint_identity` = `"telegram:user:<account_id>"` (the user's Telegram account, NOT the bot)
  - `event.external_event_id` = Telegram `message.id`
  - `event.external_thread_id` = `<chat_id>` (the dialog/group)
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = `<sender_id>` (may be the user themselves or another participant)
  - `payload.raw` = full Telethon message payload
  - `payload.normalized_text` = extracted text (HTML-escaped for XSS protection)
  - `control.idempotency_key` = derived from message ID + endpoint identity

### Requirement: Bounded Backfill on Startup
The connector supports optional historical message replay on startup to fill gaps from downtime.

#### Scenario: Backfill window configuration
- **WHEN** `CONNECTOR_BACKFILL_WINDOW_H` is configured (e.g., 24 for last 24 hours)
- **THEN** the connector fetches messages from the configured hour window across all dialogs before switching to live subscription

#### Scenario: Backfill deduplication
- **WHEN** backfill processes historical messages
- **THEN** only messages with IDs greater than `last_message_id` from the checkpoint are processed
- **AND** any duplicates are harmlessly caught by Switchboard's ingest dedup

#### Scenario: Backfill completes before live mode
- **WHEN** the connector starts with a backfill window
- **THEN** backfill completes first, then the live `NewMessage` event handler takes over
- **AND** there is no webhook or polling mode — only live subscription

### Requirement: MTProto Credential Requirements
The connector authenticates with Telegram using personal account credentials (not a bot token).

#### Scenario: Required credentials
- **WHEN** the user client connector starts
- **THEN** `TELEGRAM_API_ID` (int, from my.telegram.org), `TELEGRAM_API_HASH` (string, from my.telegram.org), and `TELEGRAM_USER_SESSION` (Telethon session string or encrypted file path) must be available

#### Scenario: DB-first credential resolution
- **WHEN** database configuration is available
- **THEN** the connector attempts DB-first resolution via `CredentialStore` for all three credentials
- **AND** falls back to environment variables if DB resolution fails

#### Scenario: Session security
- **WHEN** managing session credentials
- **THEN** session material must be stored in a secret manager or encrypted local storage — never committed to version control
- **AND** sessions must be rotated/revoked promptly after credential exposure

### Requirement: Privacy, Consent, and Data Minimization
Because this connector reads a user's personal Telegram messages, strict privacy safeguards are required.

#### Scenario: Explicit user consent
- **WHEN** the user client connector is deployed
- **THEN** explicit user consent must be obtained before enabling account-wide ingestion
- **AND** clear scope disclosure must explain which chats/types are included

#### Scenario: [TARGET-STATE] Scope controls
- **WHEN** the user client connector is configured
- **THEN** optional chat/sender allow/deny lists are available to limit ingestion scope
- **AND** per-chat filtering (allowlist/denylist), per-sender filtering, and message type filtering (e.g., exclude media, only text) are supported

#### Scenario: [TARGET-STATE] Content redaction
- **WHEN** sensitive content patterns are detected
- **THEN** optional redaction rules can filter sensitive messages before ingest submission

#### Scenario: [TARGET-STATE] Audit trail
- **WHEN** the connector's lifecycle changes (start, stop, config changes)
- **THEN** all events are recorded in an audit trail

#### Scenario: Ingestion-only — no outbound
- **WHEN** the user client connector processes messages
- **THEN** it is strictly ingestion-only — it never sends messages, replies, or performs any write action on the user's Telegram account
- **AND** outbound messaging goes through the Telegram Bot connector and Messenger butler

### Requirement: Environment Variables

#### Scenario: Required variables
- **WHEN** the Telegram user client connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=telegram`, `CONNECTOR_CHANNEL=telegram`, `CONNECTOR_ENDPOINT_IDENTITY` (user-client identity), `CONNECTOR_CURSOR_PATH` must be set
- **AND** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_USER_SESSION` must be resolvable

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_BACKFILL_WINDOW_H` (bounded startup replay window), and `CONNECTOR_HEARTBEAT_INTERVAL_S` are optionally configurable

### Requirement: Deployment Model
The user client connector runs as a dedicated daemon, separate from butler daemons.

#### Scenario: Dedicated daemon process
- **WHEN** the user client connector is deployed
- **THEN** it runs as a dedicated daemon process per user account
- **AND** its lifecycle is independent from the Switchboard process
- **AND** it communicates with Switchboard exclusively via MCP tool calls

#### Scenario: Run order
- **WHEN** starting the butler ecosystem
- **THEN** the Switchboard must be running and accepting MCP connections before starting the user client connector
- **AND** the connector verifies accepted ingest events and lag metrics after startup

### Requirement: Implementation Status

#### Scenario: Completed features
- **WHEN** evaluating the connector for deployment
- **THEN** the following are implemented: live user-client session via Telethon (MTProto), real-time message event subscription, normalization to `ingest.v1`, idempotent submission to Switchboard, durable checkpoint with restart-safe replay, bounded in-flight concurrency control, optional bounded backfill on startup, graceful degradation when Telethon not installed

#### Scenario: [TARGET-STATE] v2-only gaps
- **WHEN** evaluating for production deployment with real user accounts
- **THEN** the following remain unimplemented: privacy/consent guardrails (Section 8 of docs), explicit feature flag enforcement, per-chat/per-sender filtering, content redaction, structured metrics export, health check endpoint, lag monitoring and alerting
