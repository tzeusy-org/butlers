## MODIFIED Requirements

### Requirement: Batch Envelope Format
Flushed conversation snippets SHALL be submitted as a single ingest.v1 envelope with conversation history in the payload.

#### Scenario: Batch envelope structure
- **WHEN** a chat buffer is flushed with conversation context
- **THEN** the ingest.v1 envelope SHALL contain:
  - `event.external_event_id` = `"batch:<chat_id>:<min_msg_id>-<max_msg_id>"`
  - `event.external_conversation_id` = `"telegram:<chat_id>"`
  - `event.reply_target_ref` = `<chat_id>:<max_msg_id>` (the newest buffered message)
  - `sender.identity` = `"multiple"`
  - `payload.normalized_text` = concatenated text of NEW (buffered) messages only, with sender prefixes
  - `payload.raw.conversation_history` = ordered list of all context messages (history + new)
  - `control.idempotency_key` = `"tg_batch:<chat_id>:<min_msg_id>:<max_msg_id>"`
  - `control.payload_type` = `"conversation_history"`

#### Scenario: Conversation history entry format
- **WHEN** `payload.raw.conversation_history` is populated
- **THEN** each entry SHALL contain: `message_id`, `sender_id`, `text`, `timestamp` (ISO 8601), `is_new` (boolean), and `reply_to` (message ID or null)
- **AND** entries are ordered by `message_id` ascending

#### Scenario: is_new flag semantics
- **WHEN** a message in `conversation_history` was in the flush buffer (a newly arrived message)
- **THEN** `is_new` SHALL be `true`
- **AND** messages fetched as history context SHALL have `is_new = false`

#### Scenario: Backward compatibility
- **WHEN** a downstream consumer reads only `payload.normalized_text`
- **THEN** it receives the concatenated new messages and operates correctly without parsing `conversation_history`
