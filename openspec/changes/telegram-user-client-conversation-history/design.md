## Context

The Telegram user-client connector (`src/butlers/connectors/telegram_user_client.py`) currently processes each `NewMessage` event independently: normalize → policy gate → discretion → submit. Each submission is a single message with no surrounding conversation context.

The connector uses Telethon's MTProto client, which provides `client.get_messages(chat, limit=N)` and `message.reply_to_msg_id` for fetching history and reply chains. These are read-only operations, consistent with the connector's readonly contract.

Current flow:
```
NewMessage event → _process_message(message) → policy → discretion → normalize → submit
```

Proposed flow:
```
NewMessage event → buffer_message(chat_id, message) → [10min timer fires] → fetch_history(chat_id) → build_batch_envelope → policy → discretion → submit
```

Key constraint: the connector's ingestion policy and discretion layers currently evaluate single messages. The batch approach changes what gets evaluated — a conversation snippet rather than individual messages.

## Goals / Non-Goals

**Goals:**
- Provide butlers with conversation context (surrounding messages + reply-to chains) so factoid extraction is accurate
- Reduce Switchboard ingest volume by batching messages per-chat with a 10-minute flush interval
- Keep the connector readonly — new Telethon calls are read-only (`get_messages`, reading `reply_to_msg_id`)
- Maintain backward compatibility — existing consumers that only read `normalized_text` continue to work

**Non-Goals:**
- Changing the ingest.v1 schema definition (we add an opaque payload field, not a schema-level change)
- Adding "target message" or "question to answer" semantics — all messages are equal context
- Real-time streaming of conversation updates (we flush every 10 minutes, not on every message)
- Modifying Switchboard or downstream butler ingestion logic
- Fetching media/attachments for context (text-only for now)

## Decisions

### D1: Buffer structure — per-chat dict with asyncio.Lock

Store buffered messages in `dict[str, ChatBuffer]` keyed by chat_id. Each `ChatBuffer` holds:
- `messages: list[TelethonMessage]` — accumulated since last flush
- `last_flush_ts: float` — monotonic timestamp of last flush
- `lock: asyncio.Lock` — prevents concurrent flush + append for the same chat

**Why not a global queue?** Per-chat isolation means a slow history fetch for one chat doesn't block flushing another. It also makes the flush timer per-chat rather than global.

**Alternative: asyncio.Queue per chat** — rejected because we need random access (reply-to lookups) and the flush is timer-driven, not consumer-driven.

### D2: Flush trigger — periodic task scanning all buffers

A single `asyncio.Task` runs every 60 seconds, scanning all chat buffers. Any buffer where `now - last_flush_ts >= FLUSH_INTERVAL` (default 600s / 10 minutes) gets flushed. This is simpler than per-chat timers and avoids timer proliferation in high-chat-count scenarios.

On flush, the buffer is atomically swapped (take messages, reset list) so new messages arriving during history fetch go into the next batch.

**On connector stop**: force-flush all non-empty buffers to avoid data loss.

### D3: History window — max(time_bound, message_count)

When flushing a chat buffer, fetch conversation history using:
```python
history = await client.get_messages(
    chat_id,
    limit=HISTORY_MAX_MESSAGES,      # default: 50
    offset_date=oldest_buffered_msg_date - timedelta(minutes=HISTORY_TIME_WINDOW_M),  # default: 30
)
```

Then merge: `context_messages = dedupe(history + buffered_messages)`, sorted by message ID.

The "max" semantics come naturally: `get_messages(limit=50)` returns up to 50 messages going back as far as needed, while `offset_date` ensures we don't go further back than the time window. We take the union.

**Config env vars:**
- `TELEGRAM_USER_HISTORY_MAX_MESSAGES` (default: 50)
- `TELEGRAM_USER_HISTORY_TIME_WINDOW_M` (default: 30)
- `TELEGRAM_USER_FLUSH_INTERVAL_S` (default: 600)

### D4: Reply-to resolution — single-level fetch

For each buffered message with `reply_to_msg_id`, fetch the replied-to message if it's not already in the history window. Only resolve one level (not reply chains of reply chains) to bound API calls.

```python
reply_ids = {m.reply_to_msg_id for m in buffered if m.reply_to_msg_id}
missing = reply_ids - {m.id for m in context_messages}
for mid in missing:
    reply_msg = await client.get_messages(chat_id, ids=mid)
    if reply_msg:
        context_messages.append(reply_msg)
```

### D5: Batch envelope format

The ingest.v1 envelope for a conversation snippet:

```python
{
    "schema_version": "ingest.v1",
    "source": { ... },  # unchanged
    "event": {
        "external_event_id": f"batch:{chat_id}:{min_id}-{max_id}",
        "external_thread_id": chat_id,
        "observed_at": flush_timestamp,
    },
    "sender": {
        "identity": "multiple",  # batch contains multiple senders
    },
    "payload": {
        "raw": {},  # omit raw for batches (too large)
        "normalized_text": concatenated_new_messages,  # only the NEW buffered messages
        "conversation_history": [
            {
                "message_id": msg.id,
                "sender_id": msg.sender_id,
                "text": msg.message,
                "timestamp": msg.date.isoformat(),
                "is_new": msg.id in buffered_ids,  # distinguishes new vs context
                "reply_to": msg.reply_to_msg_id,
            }
            for msg in sorted_context
        ],
    },
    "control": {
        "idempotency_key": f"tg_batch:{chat_id}:{min_id}:{max_id}",
        "policy_tier": "default",
    },
}
```

`normalized_text` contains only the NEW messages (the buffered ones), concatenated with newlines and sender prefixes. This is what downstream butlers see if they don't parse `conversation_history`.

`conversation_history` contains ALL context messages (history + new), with `is_new` flag to distinguish.

### D6: Policy and discretion evaluation — on the concatenated batch

Ingestion policy is evaluated once per flush using the chat_id (same as today — `_build_ingestion_envelope` uses chat_id as `raw_key`).

Discretion is evaluated on the concatenated `normalized_text` of the NEW messages only. This is a natural fit — the discretion LLM decides whether the new conversation snippet is worth forwarding.

### D7: Checkpoint advancement

Checkpoint (`_last_message_id`) advances to `max(msg.id for msg in buffered_messages)` after successful batch submission. This is safe because:
- Buffered messages are always newer than the checkpoint (NewMessage events are monotonically increasing per chat)
- History messages fetched for context are older and don't affect the checkpoint

## Risks / Trade-offs

**[Risk] Delayed ingestion (10-minute buffer)** → Acceptable for this connector's use case (passive contextual awareness, not interactive). The butler doesn't need to respond to these messages in real-time. Configurable via `TELEGRAM_USER_FLUSH_INTERVAL_S`.

**[Risk] Telethon rate limits on `get_messages`** → Telegram's flood-wait limits apply. Mitigation: cap history fetches at 50 messages, add a small delay between chat flushes, and catch `FloodWaitError` with backoff retry. A single flush cycle processes chats sequentially, not in parallel.

**[Risk] Memory usage from buffering** → In very high-traffic scenarios, buffers could grow large. Mitigation: add a per-chat cap (e.g., 200 messages). If exceeded, force-flush early. Text-only storage keeps per-message size small.

**[Risk] Missed messages on crash before flush** → If the connector crashes, unbuffered messages are lost (checkpoint hasn't advanced). Mitigation: on restart, the backfill window covers the gap. The existing `CONNECTOR_BACKFILL_WINDOW_H` already handles this scenario.

**[Trade-off] Duplicate history in overlapping flushes** → Two consecutive flushes for the same chat will have overlapping history windows. This is harmless — butlers are idempotent on factoid extraction, and the batch idempotency key includes the message ID range so Switchboard deduplicates.
