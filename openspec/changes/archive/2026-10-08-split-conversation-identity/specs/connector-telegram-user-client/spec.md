## MODIFIED Requirements

### Requirement: ingest.v1 Field Mapping
Each user-client message SHALL be normalized to the canonical `ingest.v1` envelope.

#### Scenario: Field mapping
- **WHEN** a user-client message is normalized
- **THEN** the mapping is:
  - `source.channel` = `"telegram_user_client"`
  - `source.provider` = `"telegram"`
  - `source.endpoint_identity` = `"telegram:user:<account_id>"` (the user's Telegram account, NOT the bot)
  - `event.external_event_id` = Telegram `message.id`
  - `event.external_conversation_id` = `"telegram:<chat_id>"` (the dialog/group)
  - `event.reply_target_ref` = `<chat_id>:<message_id>`
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = `<sender_id>` (may be the user themselves or another participant)
  - `payload.raw` = full Telethon message payload
  - `payload.normalized_text` = extracted text (HTML-escaped for XSS protection)
  - `control.idempotency_key` = derived from message ID + endpoint identity
