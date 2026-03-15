## Why

The Telegram user-client connector currently submits each message individually to Switchboard as a standalone ingest.v1 envelope. Butlers receiving these isolated messages lack conversational context — they can't tell whether "yes, Thursday works" is about a dinner plan or a dentist appointment without seeing the surrounding conversation. Unlike the bot connector (where messages are targeted questions), user-client messages are passive snippets that butlers parse for factoids. Conversation context is essential for accurate extraction.

Additionally, high-traffic chats generate a flood of individual ingest submissions. Buffering and batching reduces load on Switchboard and downstream butlers while providing richer context per submission.

## What Changes

- **Conversation history window**: When flushing buffered messages for a chat, fetch surrounding conversation context bounded by `max(time_window, message_count)` — e.g., last 30 minutes or last 50 messages, whichever is larger.
- **Reply-to resolution**: If any buffered message is a reply, fetch the replied-to message and include it in the context window (even if it falls outside the time/count bounds).
- **10-minute flush buffer**: Accumulate incoming messages per-chat in a buffer. Flush the buffer for a chat when it has been ≥10 minutes since the last flush for that chat (not 10 minutes since the first message — we want a rolling window). Individual messages are not submitted immediately.
- **Batch envelope format**: The ingest.v1 envelope's `payload` gains a `conversation_history` field containing the ordered context messages, while `normalized_text` contains the concatenated new (buffered) messages. The envelope represents a "conversation snippet" rather than a single message.
- **No concept of "target message"**: The butler's job is to parse the entire snippet for useful factoids. There is no distinction between "the message to respond to" and "context" — everything is context.

## Capabilities

### New Capabilities
- `telegram-user-client-conversation-history`: Conversation history windowing, reply-to resolution, and 10-minute flush buffering for the Telegram user-client connector.

### Modified Capabilities
<!-- None — this is additive behavior within the existing connector. The existing connector-telegram-user-client spec's message processing pipeline is unchanged; we're adding a buffering + context layer before the existing normalize → submit flow. -->

## Impact

- **`src/butlers/connectors/telegram_user_client.py`**: Major changes — add per-chat message buffer, flush timer, history fetching logic, reply-to resolution, batch envelope construction. The `_process_message` flow changes from immediate-submit to buffer-and-flush.
- **ingest.v1 envelope schema**: `payload.conversation_history` field added (backward-compatible — existing consumers ignore unknown fields).
- **Switchboard ingest**: No changes required — the envelope is still valid ingest.v1; the new field is opaque payload data.
- **Downstream butlers**: Butlers that consume telegram_user_client ingestion events will see richer `payload` with conversation history. No breaking changes — butlers that only read `normalized_text` continue to work.
- **Telethon API usage**: New calls to `client.get_messages()` for history fetching and `message.get_reply_message()` for reply resolution. These are read-only operations consistent with the connector's readonly contract.
- **Checkpoint semantics**: Checkpoint must track the highest message ID across all buffered+flushed messages, not just the last processed message.
