## REMOVED Requirements

### Requirement: Batch Envelope Format

Superseded by `Stable Batch Envelope Format` because reply targeting is no
longer used as conversation identity.

## ADDED Requirements

### Requirement: Stable Batch Envelope Format

Flushed conversation snippets SHALL be submitted as one `ingest.v1` envelope.

#### Scenario: Batch envelope structure

- **WHEN** a chat buffer is flushed
- **THEN** `event.external_conversation_id` SHALL be `"telegram:<chat_id>"`
- **AND** `event.reply_target_ref` SHALL be `<chat_id>:<latest_message_id>`
- **AND** the event ID, sender, normalized text, conversation history, idempotency key, and payload type SHALL preserve the established batch mappings

#### Scenario: Conversation history entry format

- **WHEN** conversation history is populated
- **THEN** entries SHALL retain their ordered message, sender, text, timestamp, newness, and reply fields

#### Scenario: is_new flag semantics

- **WHEN** a message came from the flush buffer
- **THEN** `is_new` SHALL distinguish it from fetched context

#### Scenario: Backward compatibility

- **WHEN** a consumer reads only normalized text
- **THEN** it SHALL still receive the concatenated new messages
