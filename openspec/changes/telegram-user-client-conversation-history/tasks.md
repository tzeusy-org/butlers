## 1. ChatBuffer Data Structure

- [ ] 1.1 Create `ChatBuffer` dataclass in `telegram_user_client.py` with fields: `messages: list`, `last_flush_ts: float`, `lock: asyncio.Lock`
- [ ] 1.2 Add `_chat_buffers: dict[str, ChatBuffer]` to `TelegramUserClientConnector.__init__`
- [ ] 1.3 Add config parsing for new env vars: `TELEGRAM_USER_FLUSH_INTERVAL_S`, `TELEGRAM_USER_HISTORY_MAX_MESSAGES`, `TELEGRAM_USER_HISTORY_TIME_WINDOW_M`, `TELEGRAM_USER_BUFFER_MAX_MESSAGES`

## 2. Message Buffering

- [ ] 2.1 Replace immediate `_process_message` submission with buffer-append logic: extract chat_id, append message to `_chat_buffers[chat_id]`, check buffer cap for force-flush
- [ ] 2.2 Implement `_buffer_message(message)` method that handles chat buffer creation, append, and force-flush on cap exceeded

## 3. Flush Timer

- [ ] 3.1 Implement `_flush_scanner_loop()` async task: runs every 60s, iterates all chat buffers, flushes any where `now - last_flush_ts >= flush_interval`
- [ ] 3.2 Start flush scanner task in `start()`, cancel in `stop()`
- [ ] 3.3 Implement force-flush of all non-empty buffers in `stop()` for graceful shutdown

## 4. History Fetching

- [ ] 4.1 Implement `_fetch_conversation_history(chat_id, buffered_messages) -> list[Message]`: calls `client.get_messages()` with limit and offset_date bounds, merges with buffered, deduplicates by message ID
- [ ] 4.2 Add `FloodWaitError` handling with backoff and fail-open (proceed without history)
- [ ] 4.3 Add sequential delay between chat flushes to avoid Telegram rate limits

## 5. Reply-To Resolution

- [ ] 5.1 Implement `_resolve_reply_tos(chat_id, buffered_messages, context_messages) -> list[Message]`: collect reply_to_msg_ids from buffered messages, fetch missing ones, append to context
- [ ] 5.2 Limit to single-level resolution (no recursive chains)

## 6. Batch Envelope Construction

- [ ] 6.1 Implement `_build_batch_envelope(chat_id, buffered_messages, context_messages) -> dict`: construct ingest.v1 envelope with `conversation_history` field, batch idempotency key, and concatenated `normalized_text`
- [ ] 6.2 Format `normalized_text` with sender prefixes (e.g., `[sender_id]: message text`)
- [ ] 6.3 Populate `conversation_history` entries with `message_id`, `sender_id`, `text`, `timestamp`, `is_new`, `reply_to`

## 7. Flush Pipeline

- [ ] 7.1 Implement `_flush_chat_buffer(chat_id)`: atomically swap buffer, fetch history, resolve reply-tos, build batch envelope, evaluate policy + discretion, submit, advance checkpoint
- [ ] 7.2 Wire ingestion policy evaluation to use chat_id as raw_key (same as current)
- [ ] 7.3 Wire discretion evaluation on concatenated new-message text
- [ ] 7.4 Advance checkpoint to max message ID of buffered messages on successful submission

## 8. Tests

- [ ] 8.1 Unit test: `ChatBuffer` creation, append, cap detection
- [ ] 8.2 Unit test: `_buffer_message` routes to correct chat buffer, creates new buffer for unknown chat
- [ ] 8.3 Unit test: flush scanner identifies buffers past interval, skips empty/recent buffers
- [ ] 8.4 Unit test: `_fetch_conversation_history` merges and deduplicates correctly
- [ ] 8.5 Unit test: `_resolve_reply_tos` fetches missing reply-to messages, skips already-present ones
- [ ] 8.6 Unit test: `_build_batch_envelope` produces correct ingest.v1 structure with conversation_history
- [ ] 8.7 Unit test: `_flush_chat_buffer` end-to-end with mocked Telethon client
- [ ] 8.8 Unit test: graceful shutdown flushes all non-empty buffers
- [ ] 8.9 Unit test: history fetch failure degrades gracefully (proceeds without context)
- [ ] 8.10 Unit test: force-flush on buffer cap exceeded
