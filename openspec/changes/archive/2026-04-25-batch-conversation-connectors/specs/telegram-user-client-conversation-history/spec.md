## MODIFIED Requirements

### Requirement: Timed Flush Interval
The connector SHALL flush each chat's buffer after a configurable time interval (default: 30 minutes).

#### Scenario: Flush interval trigger
- **WHEN** >= `flush_interval_s` seconds (default: 1800) have elapsed since the last flush for chat `C`
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
- **AND** the fetch window extends back to at least `TELEGRAM_USER_HISTORY_TIME_WINDOW_M` minutes (default: 35) before the oldest buffered message

#### Scenario: History merged with buffered messages
- **WHEN** history is fetched for a flush
- **THEN** history messages and buffered messages are merged into a single ordered list
- **AND** duplicate messages (same message ID) are deduplicated

#### Scenario: History fetch fails gracefully
- **WHEN** the Telethon `get_messages()` call fails (e.g., `FloodWaitError`, network error)
- **THEN** the flush proceeds with only the buffered messages (no history context)
- **AND** the failure is logged as a warning

### Requirement: Batch Envelope Format
Flushed conversation snippets SHALL be submitted as a single ingest.v1 envelope with conversation history in the payload and a payload type tag.

#### Scenario: Batch envelope structure
- **WHEN** a chat buffer is flushed with conversation context
- **THEN** the ingest.v1 envelope SHALL contain:
  - `event.external_event_id` = `"batch:<chat_id>:<min_msg_id>-<max_msg_id>"`
  - `event.external_thread_id` = `<chat_id>`
  - `sender.identity` = `"multiple"`
  - `payload.normalized_text` = concatenated text of NEW (buffered) messages only, with sender prefixes
  - `payload.conversation_history` = ordered list of all context messages (history + new)
  - `control.idempotency_key` = `"tg_batch:<chat_id>:<min_msg_id>:<max_msg_id>"`
  - `control.payload_type` = `"conversation_history"`

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

### Requirement: Configuration Environment Variables

#### Scenario: New environment variables
- **WHEN** the connector starts
- **THEN** the following environment variables are recognized:
  - `TELEGRAM_USER_FLUSH_INTERVAL_S` (default: 1800) — seconds between flushes per chat
  - `TELEGRAM_USER_HISTORY_MAX_MESSAGES` (default: 50) — max messages to fetch for history context
  - `TELEGRAM_USER_HISTORY_TIME_WINDOW_M` (default: 35) — minutes to look back for history context
  - `TELEGRAM_USER_BUFFER_MAX_MESSAGES` (default: 200) — per-chat buffer cap before force-flush
