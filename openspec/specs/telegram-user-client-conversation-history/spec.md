# Telegram User Client Conversation History

## Purpose

Extends the Telegram user client connector with per-chat message buffering, timed batch flushing, and surrounding conversation history context. Instead of submitting each message individually to the Switchboard, the connector accumulates messages and flushes them as conversation snippets with history context for improved routing accuracy and downstream comprehension.

## ADDED Requirements

### Requirement: Per-Chat Message Buffering
The connector SHALL accumulate incoming messages in per-chat buffers instead of submitting each message individually to Switchboard.

#### Scenario: Message arrives and is buffered
- **WHEN** a `NewMessage` event fires for chat `C`
- **THEN** the message is appended to the buffer for chat `C`
- **AND** the message is NOT immediately submitted to Switchboard

#### Scenario: Buffer isolation between chats
- **WHEN** messages arrive for chats `C1` and `C2`
- **THEN** each chat maintains an independent buffer
- **AND** flushing `C1` does not affect `C2`'s buffer

#### Scenario: Per-chat buffer cap
- **WHEN** a chat buffer exceeds 200 messages before the flush interval
- **THEN** the buffer SHALL be force-flushed immediately to prevent unbounded memory growth

### Requirement: Timed Flush Interval
The connector SHALL flush each chat's buffer after a configurable time interval (default: 10 minutes).

#### Scenario: Flush interval trigger
- **WHEN** >= `TELEGRAM_USER_FLUSH_INTERVAL_S` seconds (default: 600) have elapsed since the last flush for chat `C`
- **AND** chat `C`'s buffer is non-empty
- **THEN** the connector flushes chat `C`'s buffer

#### Scenario: Periodic scan
- **WHEN** the flush scanner task runs (every 60 seconds)
- **THEN** it checks all chat buffers and flushes any that have exceeded the flush interval

#### Scenario: Empty buffer skipped
- **WHEN** the flush interval has elapsed for chat `C`
- **AND** chat `C`'s buffer is empty
- **THEN** no flush occurs and no ingest submission is made

#### Scenario: Graceful shutdown flush
- **WHEN** the connector is stopping (`stop()` is called)
- **THEN** all non-empty chat buffers SHALL be force-flushed before shutdown completes

### Requirement: Conversation History Window
On flush, the connector SHALL fetch surrounding conversation context from the chat, bounded by configurable time and message-count limits.

#### Scenario: History fetch on flush
- **WHEN** chat `C`'s buffer is flushed
- **THEN** the connector fetches up to `TELEGRAM_USER_HISTORY_MAX_MESSAGES` (default: 50) recent messages from chat `C`
- **AND** the fetch window extends back to at least `TELEGRAM_USER_HISTORY_TIME_WINDOW_M` minutes (default: 30) before the oldest buffered message

#### Scenario: History merged with buffered messages
- **WHEN** history is fetched for a flush
- **THEN** history messages and buffered messages are merged into a single ordered list
- **AND** duplicate messages (same message ID) are deduplicated

#### Scenario: History fetch fails gracefully
- **WHEN** the Telethon `get_messages()` call fails (e.g., `FloodWaitError`, network error)
- **THEN** the flush proceeds with only the buffered messages (no history context)
- **AND** the failure is logged as a warning

### Requirement: Reply-To Resolution
On flush, the connector SHALL fetch replied-to messages that are not already in the conversation window.

#### Scenario: Reply-to message fetched
- **WHEN** a buffered message has `reply_to_msg_id` set
- **AND** that message ID is not already in the conversation history window
- **THEN** the connector fetches the replied-to message via `client.get_messages(chat, ids=reply_to_msg_id)`
- **AND** includes it in the conversation history

#### Scenario: Single-level resolution only
- **WHEN** a replied-to message itself replies to another message
- **THEN** only the first-level reply is fetched (no recursive chain resolution)

#### Scenario: Reply-to fetch fails gracefully
- **WHEN** fetching a replied-to message fails
- **THEN** the flush proceeds without that message
- **AND** the failure is logged as a debug message

### Requirement: Batch Envelope Format
Flushed conversation snippets SHALL be submitted as a single ingest.v1 envelope with conversation history in the payload.

#### Scenario: Batch envelope structure
- **WHEN** a chat buffer is flushed with conversation context
- **THEN** the ingest.v1 envelope SHALL contain:
  - `event.external_event_id` = `"batch:<chat_id>:<min_msg_id>-<max_msg_id>"`
  - `event.external_thread_id` = `<chat_id>`
  - `sender.identity` = `"multiple"`
  - `payload.normalized_text` = concatenated text of NEW (buffered) messages only, with sender prefixes
  - `payload.conversation_history` = ordered list of all context messages (history + new)
  - `control.idempotency_key` = `"tg_batch:<chat_id>:<min_msg_id>:<max_msg_id>"`

#### Scenario: Conversation history entry format
- **WHEN** `payload.conversation_history` is populated
- **THEN** each entry SHALL contain: `message_id`, `sender_id`, `text`, `timestamp` (ISO 8601), `is_new` (boolean), and `reply_to` (message ID or null)
- **AND** entries are ordered by `message_id` ascending

#### Scenario: is_new flag semantics
- **WHEN** a message in `conversation_history` was in the flush buffer (a newly arrived message)
- **THEN** `is_new` SHALL be `true`
- **AND** messages fetched as history context SHALL have `is_new = false`

#### Scenario: Backward compatibility
- **WHEN** a downstream consumer reads only `payload.normalized_text`
- **THEN** it receives the concatenated new messages and operates correctly without parsing `conversation_history`

### Requirement: Policy and Discretion on Batch
Ingestion policy and discretion evaluation SHALL operate on the batch as a whole.

#### Scenario: Ingestion policy evaluation
- **WHEN** a chat buffer is flushed
- **THEN** the ingestion policy evaluator receives the chat_id as `raw_key` (same as current single-message evaluation)
- **AND** policy decisions apply to the entire batch (not per-message)

#### Scenario: Discretion evaluation on new messages
- **WHEN** the discretion layer is enabled
- **THEN** discretion is evaluated on the concatenated `normalized_text` (new messages only)
- **AND** an IGNORE verdict drops the entire batch

### Requirement: Checkpoint Advancement
The checkpoint SHALL advance only after successful batch submission.

#### Scenario: Checkpoint after flush
- **WHEN** a batch is successfully submitted to Switchboard
- **THEN** `_last_message_id` advances to the maximum message ID among the buffered (new) messages

#### Scenario: Checkpoint not advanced on failure
- **WHEN** batch submission fails
- **THEN** the checkpoint is NOT advanced
- **AND** the buffered messages remain available for retry on the next flush cycle

### Requirement: Configuration Environment Variables

#### Scenario: New environment variables
- **WHEN** the connector starts
- **THEN** the following environment variables are recognized:
  - `TELEGRAM_USER_FLUSH_INTERVAL_S` (default: 600) — seconds between flushes per chat
  - `TELEGRAM_USER_HISTORY_MAX_MESSAGES` (default: 50) — max messages to fetch for history context
  - `TELEGRAM_USER_HISTORY_TIME_WINDOW_M` (default: 30) — minutes to look back for history context
  - `TELEGRAM_USER_BUFFER_MAX_MESSAGES` (default: 200) — per-chat buffer cap before force-flush
