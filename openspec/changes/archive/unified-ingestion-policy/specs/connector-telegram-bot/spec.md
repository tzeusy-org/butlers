## MODIFIED Requirements

### Requirement: Source Filter Integration (Telegram Bot)

The Telegram bot connector implements the ingestion policy gate using `IngestionPolicyEvaluator` with `scope = 'connector:telegram-bot:<endpoint_identity>'`. It builds an `IngestionEnvelope` from the Telegram update's chat ID. Only the `chat_id` rule type is valid for Telegram bot connector scope.

#### Scenario: IngestionPolicyEvaluator instantiation
- **WHEN** the Telegram bot connector initializes
- **THEN** it creates an `IngestionPolicyEvaluator` with `scope = 'connector:telegram-bot:<endpoint_identity>'` and the shared switchboard DB pool

#### Scenario: Filter gate position in Telegram pipeline
- **WHEN** the Telegram bot connector processes an incoming update
- **THEN** it evaluates the message via `IngestionPolicyEvaluator` AFTER update normalization and BEFORE Switchboard submission

#### Scenario: Valid rule types for Telegram bot connector scope
- **WHEN** the API validates a rule for `scope = 'connector:telegram-bot:...'`
- **THEN** only the `chat_id` rule type is accepted

#### Scenario: Envelope construction from Telegram update
- **WHEN** the Telegram bot connector builds an `IngestionEnvelope`
- **THEN** `sender_address` is empty, `source_channel = "telegram"`, `raw_key` is `str(chat.id)` extracted from message/edited_message/channel_post, `headers` and `mime_parts` are empty

#### Scenario: Chat ID extraction from various update types
- **WHEN** the update contains a `message`, `edited_message`, or `channel_post` with a `chat.id`
- **THEN** the `raw_key` is set to the stringified chat ID (e.g., `"987654321"` or `"-100987654321"`)
