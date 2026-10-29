## MODIFIED Requirements

### Requirement: ingest.v1 Field Mapping
Each Telegram update SHALL be normalized to the canonical `ingest.v1` envelope.

#### Scenario: Field mapping
- **WHEN** a Telegram update is normalized
- **THEN** the mapping is:
  - `source.channel` = `"telegram_bot"`
  - `source.provider` = `"telegram"`
  - `source.endpoint_identity` = receiving bot identity
  - `event.external_event_id` = Telegram `update_id`
  - `event.external_conversation_id` = `telegram:<chat_id>`, suffixed `:topic:<message_thread_id>` only when Telegram marks the message `is_topic_message`
  - `event.reply_target_ref` = `<chat_id>:<message_id>` (bare `<chat_id>` when the message ID is absent)
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = `message.from.id` (Telegram sender user ID)
  - `payload.raw` = full Telegram update JSON
  - `payload.normalized_text` = extracted text (see tiered extraction)
  - `control.idempotency_key` = `"tg:<chat_id>:<message_id>"` (canonical across the bot and user-client connectors so the same Telegram message dedupes identically; falls back to `"telegram:<endpoint_identity>:<update_id>"` only when chat_id or message_id is unavailable)
  - `control.policy_tier` = `"interactive"` (bot messages are direct user-to-bot interactions)
- **AND** the envelope SHALL NOT carry `event.external_thread_id`

#### Scenario: Conversation identity and reply targets remain distinct
- **WHEN** two Telegram updates carry different messages from the same chat, or from the same forum topic
- **THEN** both envelopes SHALL have the same `event.external_conversation_id`
- **AND** each envelope SHALL have a distinct `event.reply_target_ref`
- **AND** a message in a reply thread that Telegram does not mark `is_topic_message` SHALL keep its chat's conversation identity
- **AND** a `telegram_bot` envelope missing either field SHALL be rejected at the `ingest.v1` boundary
