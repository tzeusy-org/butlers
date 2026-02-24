# Telegram Bot Connector

## Purpose
The Telegram Bot connector is the butler ecosystem's primary user-facing chat interface. It receives Telegram updates from a bot account, normalizes them into `ingest.v1` envelopes for Switchboard ingestion, and provides the bidirectional channel through which butlers communicate with the user. The bot account is the "face" of the butler system on Telegram — users message the bot, and butlers reply through the Messenger butler's channel tools. This connector handles the inbound half of that loop.

## ADDED Requirements

### Requirement: Telegram Bot Identity and Role
The Telegram bot connector bridges a Telegram bot account into the butler ecosystem as the primary interactive chat channel.

#### Scenario: Bot as user-facing interface
- **WHEN** the Telegram bot connector runs
- **THEN** it ingests messages sent TO the butler's Telegram bot (user → bot direction)
- **AND** the same bot token is used by the Messenger butler's `telegram_send_message` and `telegram_reply_to_message` tools for outbound delivery (bot → user direction)
- **AND** the connector owns the inbound half; the Messenger butler owns the outbound half

#### Scenario: Connector identity
- **WHEN** the Telegram bot connector starts
- **THEN** `source.channel="telegram"`, `source.provider="telegram"`, and `source.endpoint_identity` identifies the receiving bot (e.g., bot username or configured bot ID)

### Requirement: Update Retrieval Modes
The connector supports two modes: polling for development and webhook for production.

#### Scenario: Polling mode (development)
- **WHEN** no webhook URL is configured
- **THEN** the connector calls Telegram `getUpdates` in a loop at `CONNECTOR_POLL_INTERVAL_S` (default 1.0 second for Telegram)
- **AND** checkpoint (`last_update_id`) is loaded from and persisted to `CONNECTOR_CURSOR_PATH`
- **AND** no public URL or HTTPS is required

#### Scenario: Webhook mode (production)
- **WHEN** `CONNECTOR_WEBHOOK_URL` is configured (e.g., `https://<public-domain>/<telegram-webhook-path>`)
- **THEN** the connector calls Telegram `setWebhook` on startup with the configured URL
- **AND** the daemon must be reachable on that public HTTPS URL
- **AND** incoming webhook updates are processed individually via `process_webhook_update()`

### Requirement: ingest.v1 Field Mapping
Each Telegram update is normalized to the canonical `ingest.v1` envelope.

#### Scenario: Field mapping
- **WHEN** a Telegram update is normalized
- **THEN** the mapping is:
  - `source.channel` = `"telegram"`
  - `source.provider` = `"telegram"`
  - `source.endpoint_identity` = receiving bot identity
  - `event.external_event_id` = Telegram `update_id`
  - `event.external_thread_id` = `<chat_id>:<message_id>`
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = `message.from.id` (Telegram sender user ID)
  - `payload.raw` = full Telegram update JSON
  - `payload.normalized_text` = extracted text (see tiered extraction)
  - `control.idempotency_key` = `"telegram:<endpoint_identity>:<update_id>"`
  - `control.policy_tier` = `"default"`

### Requirement: Tiered Text Extraction
The connector extracts human-readable text from Telegram messages using a four-tier fallback strategy.

#### Scenario: Tier 1 — text field
- **WHEN** a Telegram message has a `text` field
- **THEN** it is used as `payload.normalized_text`

#### Scenario: Tier 2 — caption field
- **WHEN** a message has no `text` but has a `caption` (media with caption)
- **THEN** the caption is used as `payload.normalized_text`

#### Scenario: Tier 3 — media type descriptor
- **WHEN** a message has neither text nor caption but contains media
- **THEN** a synthesized descriptor is generated from `_MEDIA_TYPE_LABELS`:
  - `[Photo]`, `[Video]`, `[Document]`, `[Audio]`, `[GIF]`, `[Location]`, `[Dice]`
  - `[Voice message]`, `[Video message]`
  - `[Sticker: <emoji>]` (includes the sticker's emoji)
  - `[Contact: <name>]` (includes the contact's display name)
  - `[Poll: <question>]` (includes the poll question text)

#### Scenario: Tier 4 — service messages (skipped)
- **WHEN** a message has no extractable content (service messages, non-message updates)
- **THEN** the update is silently skipped — no ingest submission

### Requirement: Update Type Handling
The connector processes a defined subset of Telegram update types.

#### Scenario: Processed update types
- **WHEN** a Telegram update arrives
- **THEN** the connector processes: `message`, `edited_message`, and `channel_post`

#### Scenario: Skipped update types
- **WHEN** a non-message update arrives (`callback_query`, `inline_query`, `chosen_inline_result`, etc.)
- **THEN** it is silently skipped

### Requirement: Lifecycle Reactions
The connector supports best-effort emoji reactions on ingested messages to provide visual feedback on processing status.

#### Scenario: Reaction emoji mapping
- **WHEN** an ingested message progresses through the pipeline
- **THEN** reactions are applied:
  - In-progress: eyes emoji (`:eye:`)
  - Success: checkmark emoji (`:done:`)
  - Failure: alien emoji (`:space invader:`)

#### Scenario: Reaction API failure
- **WHEN** Telegram rejects a reaction (e.g., unsupported chat type, expected 400 error)
- **THEN** processing continues and a debug log is emitted (non-fatal — reactions are best-effort)

### Requirement: Error Handling and Backoff
The connector handles Telegram API errors with rate limit awareness and exponential backoff.

#### Scenario: Rate limit handling (HTTP 429)
- **WHEN** Telegram returns HTTP 429
- **THEN** the connector reads `Retry-After` from headers or response body `parameters.retry_after`
- **AND** sleeps for the specified duration before the next poll

#### Scenario: Conflict handling (HTTP 409)
- **WHEN** Telegram returns HTTP 409 on `getUpdates`
- **THEN** the connector skips the poll cycle with a descriptive warning
- **AND** the warning suggests checking for webhook conflicts or duplicate polling processes

#### Scenario: Exponential backoff on consecutive failures
- **WHEN** consecutive polling failures occur
- **THEN** backoff is exponential (`poll_interval * 2^failures`) with +/-10% jitter, capped at 60 seconds
- **AND** the backoff counter resets on the first successful poll

### Requirement: Credential Resolution
Bot token credentials are resolved via the layered credential store.

#### Scenario: DB-first credential resolution
- **WHEN** the connector starts with database configuration available (`DATABASE_URL` or `POSTGRES_HOST`)
- **THEN** it attempts to resolve `BUTLER_TELEGRAM_TOKEN` from DB via `CredentialStore` (layered: connector-local DB, then shared DB)
- **AND** falls back to environment variable `BUTLER_TELEGRAM_TOKEN` if DB resolution fails or is not configured

#### Scenario: Token env var from module config
- **WHEN** the butler's `butler.toml` configures `modules.telegram.bot.token_env`
- **THEN** that env var name (default `BUTLER_TELEGRAM_TOKEN`) is the lookup key for credential resolution

### Requirement: Health State Derivation
The connector reports health state based on source API connectivity and recent failure history.

#### Scenario: Health states
- **WHEN** the connector's health is queried
- **THEN** `error` when `_source_api_ok=False` (cannot reach Telegram API), `degraded` when `_consecutive_failures > 0` (recent failures but recovering), `healthy` otherwise

#### Scenario: Health and metrics server
- **WHEN** the connector is running
- **THEN** it exposes a FastAPI health server on `CONNECTOR_HEALTH_PORT` (default 40081) with `/health` (JSON status) and `/metrics` (Prometheus text format) endpoints

### Requirement: Environment Variables
Configuration via environment variables with base connector variables plus Telegram-specific variables.

#### Scenario: Required variables
- **WHEN** the Telegram bot connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=telegram`, `CONNECTOR_CHANNEL=telegram`, `CONNECTOR_ENDPOINT_IDENTITY` must be set
- **AND** `CONNECTOR_CURSOR_PATH` and `CONNECTOR_POLL_INTERVAL_S` are required for polling mode

#### Scenario: Telegram credential variables
- **WHEN** the bot scope is enabled
- **THEN** `BUTLER_TELEGRAM_TOKEN` (or the custom name from `modules.telegram.bot.token_env`) must be resolvable

#### Scenario: Optional variables
- **WHEN** the connector starts
- **THEN** `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40081), and `CONNECTOR_WEBHOOK_URL` (enables webhook mode) are optionally configurable

### Requirement: Idempotency and Safety
The connector guarantees at-least-once delivery with crash-safe resume.

#### Scenario: Dedup identity
- **WHEN** a Telegram update is submitted
- **THEN** the dedupe key is based on `update_id` + `endpoint_identity`
- **AND** duplicate accepted ingest responses are treated as success, not failures

#### Scenario: Checkpoint semantics
- **WHEN** the connector processes updates
- **THEN** it persists the polling cursor/high-water mark to `CONNECTOR_CURSOR_PATH`
- **AND** the checkpoint advances only after ingest acceptance
- **AND** on restart, it replays from the last safe checkpoint (harmless due to Switchboard dedup)
