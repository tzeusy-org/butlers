# Telegram Module

## Purpose

The Telegram module provides MCP tools for sending and replying to Telegram messages, with webhook setup for production deployments and lifecycle reaction emoji support for ingest pipeline integration.

## ADDED Requirements

### Requirement: Telegram Send/Reply Tools

The module registers MCP tools for send and reply operations.

#### Scenario: Tool registration

- **WHEN** the Telegram module registers tools
- **THEN** the following tools are available:
  - `telegram_send_message` (send a message to a chat)
  - `telegram_reply_to_message` (reply to a specific message in a chat)

### Requirement: Output-Only Module Design

Ingestion is handled by the `TelegramBotConnector` via the canonical ingest API. The module does not register input tools.

#### Scenario: No input tools registered

- **WHEN** the Telegram module starts
- **THEN** no inbound polling or input tools are registered
- **AND** all inbound messages flow through the connector → switchboard pipeline

### Requirement: TelegramConfig with Credential Scoping

Configuration supports independent enable/disable per identity scope.

#### Scenario: Config structure

- **WHEN** `[modules.telegram]` is configured
- **THEN** it includes optional `webhook_url`
- **AND** `[modules.telegram.user]` with `enabled` (default false) and `token_env` (default "USER_TELEGRAM_TOKEN")
- **AND** `[modules.telegram.bot]` with `enabled` (default true) and `token_env` (default "BUTLER_TELEGRAM_TOKEN")

#### Scenario: Token env var validation

- **WHEN** `token_env` is configured
- **THEN** it must match the pattern `^[A-Za-z_][A-Za-z0-9_]*$`

### Requirement: Credential Resolution

Bot tokens are resolved at startup via CredentialStore (DB-first, then env) and cached.

#### Scenario: Startup credential resolution

- **WHEN** `on_startup` is called with a credential store
- **THEN** all configured token keys are resolved and cached in `_resolved_credentials`

#### Scenario: Bot token resolution at runtime

- **WHEN** `_get_bot_token()` is called
- **THEN** the cached credential is used first, falling back to `os.environ`
- **AND** if the bot scope is disabled, a `RuntimeError` is raised
- **AND** if no token is found, a `RuntimeError` is raised with the expected env var name

### Requirement: Telegram API Integration

The module uses httpx to call Telegram Bot API endpoints.

#### Scenario: Send message

- **WHEN** a send_message tool is invoked with `chat_id` and `text`
- **THEN** a POST to `https://api.telegram.org/bot{token}/sendMessage` is made
- **AND** the response JSON is returned

#### Scenario: Reply to message

- **WHEN** a reply_to_message tool is invoked with `chat_id`, `message_id`, and `text`
- **THEN** a sendMessage call is made with `reply_to_message_id` set

#### Scenario: Set webhook

- **WHEN** `webhook_url` is configured and `on_startup` runs
- **THEN** a POST to `setWebhook` API is made with the configured URL

### Requirement: Lifecycle Reaction Emoji Support

The module supports setting Telegram reactions for ingest pipeline lifecycle events.

#### Scenario: React for ingest events

- **WHEN** `react_for_ingest` is called with `external_thread_id` (format: `<chat_id>:<message_id>`) and a reaction key
- **THEN** the reaction emoji is mapped via `REACTION_TO_EMOJI`:
  - `:eye` -> eyes emoji (in-progress)
  - `:done` -> checkmark emoji (success)
  - `:space invader` -> alien emoji (failure)
- **AND** a `setMessageReaction` API call is made

#### Scenario: Unparseable thread identity

- **WHEN** `react_for_ingest` is called with `None` or an unparseable `external_thread_id`
- **THEN** the call is a silent no-op

#### Scenario: Reaction API failure

- **WHEN** the Telegram API rejects a reaction (e.g., unsupported chat type)
- **THEN** processing continues and a debug log is emitted (non-fatal)

