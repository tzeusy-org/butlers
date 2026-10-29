## MODIFIED Requirements

### Requirement: ingest.v1 Field Mapping
Each Telegram update SHALL be normalized to the canonical `ingest.v1` envelope. A message carrying someone else's text SHALL mark it in `payload.untrusted_spans` (`REQ-connector-base-spec-004`); the forwarding owner SHALL stay `sender.identity`.

ID: REQ-connector-telegram-bot-001
Source: RFC 0038 D5, D6; bu-xmueeq
Scope: v1-mandatory

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
- **AND** `event.in_reply_to_ref` = `<chat_id>:<reply_to_message.message_id>` when the message replies to another message in the same chat
- **AND** `payload.untrusted_spans` is present only when the message carries forwarded or quoted text

#### Scenario: Conversation identity and reply targets remain distinct
- **WHEN** two Telegram updates carry different messages from the same chat, or from the same forum topic
- **THEN** both envelopes SHALL have the same `event.external_conversation_id`
- **AND** each envelope SHALL have a distinct `event.reply_target_ref`
- **AND** a message in a reply thread that Telegram does not mark `is_topic_message` SHALL keep its chat's conversation identity
- **AND** a `telegram_bot` envelope missing either field SHALL be rejected at the `ingest.v1` boundary

#### Scenario: Forwarded message is marked and the owner stays the sender
- **WHEN** the owner forwards a message to the bot (`forward_origin`, the legacy `forward_from`, `forward_from_chat` or `forward_sender_name` fields, or `is_automatic_forward`)
- **THEN** the whole `payload.normalized_text` SHALL be one `forwarded` span, carrying the original author's identity and display name when Telegram names one
- **AND** a forwarded media message without a caption SHALL still be marked over its descriptor text
- **AND** `sender.identity` SHALL remain the forwarding owner's `message.from.id`

#### Scenario: Quoted text is marked by its entity range
- **WHEN** the message text or caption carries a `blockquote` or `expandable_blockquote` entity
- **THEN** each such entity SHALL become a `quoted` span over the same characters, its UTF-16 offsets mapped onto `normalized_text`

#### Scenario: Reply to the bot carries a reference, not a quoted span
- **WHEN** the owner replies to a message in the same chat, including the bot's own message
- **THEN** the envelope SHALL carry `event.in_reply_to_ref` for the server to resolve
- **AND** the replied-to text SHALL NOT be marked, since it is not part of `normalized_text`

#### Scenario: Reply to a message in another chat marks the whole text
- **WHEN** the message carries `external_reply` (a reply to a message in another chat)
- **THEN** the whole `payload.normalized_text` SHALL be one `quoted` span, because the server cannot resolve the replied-to message
