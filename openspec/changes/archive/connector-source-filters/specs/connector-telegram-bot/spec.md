# Telegram Bot Connector (delta)

## ADDED Requirements

### Requirement: Source Filter Integration (Telegram Bot)
The Telegram bot connector MUST implement the source filter gate using the chat or sender ID as the evaluated key, with support for the `chat_id` key type.

#### Scenario: Valid source key types for Telegram bot connector
- **WHEN** source filters are configured for a Telegram bot connector
- **THEN** the only valid `source_key_type` is `"chat_id"`
- **AND** filters with any other `source_key_type` are skipped with a one-time WARNING log (they are incompatible with the Telegram channel)

#### Scenario: Key extraction for chat_id filters
- **WHEN** the filter gate evaluates a Telegram update
- **THEN** the connector extracts `str(update.message.chat.id)` as the key value
- **AND** for private chats (where `chat.type == "private"`), `chat.id` equals `from.id` — both identify the same individual
- **AND** for group chats, `chat.id` identifies the group; filtering by group ID blocks or allows the entire group conversation
- **AND** the key value is always a stringified integer (e.g. `"123456789"` or `"-100987654321"` for supergroups)

#### Scenario: Filter gate position in Telegram pipeline
- **WHEN** the Telegram bot connector processes an update
- **THEN** source filter evaluation runs immediately after update normalization and BEFORE Switchboard submission
- **AND** a blocked update is not submitted to Switchboard; its `update_id` IS included in the acknowledged range so Telegram does not re-deliver it

#### Scenario: SourceFilterEvaluator instantiation
- **WHEN** the Telegram bot connector starts
- **THEN** it instantiates `SourceFilterEvaluator(connector_type="telegram-bot", endpoint_identity=<configured bot endpoint identity>, db_pool=<shared switchboard pool>)`
- **AND** performs the initial filter load before beginning the getUpdates polling loop or setting the webhook
