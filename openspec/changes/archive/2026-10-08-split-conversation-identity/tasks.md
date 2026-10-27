## 1. Wire contract

- [x] 1.1 Add `external_conversation_id` and `reply_target_ref` to `ingest.v1`, route, and notify request contexts.
- [x] 1.2 Emit the split fields from the Telegram bot, Telegram user-client, WhatsApp user-client, and Gmail connectors.

## 2. Continuity

- [x] 2.1 Key the conversation anchor and realtime history on `external_conversation_id`.
- [x] 2.2 Carry the key on outbound history rows and filtered-event replay.
- [x] 2.3 Group Relationship `interaction_sync` chats on the key.

## 3. Data

- [x] 3.1 Add the reversible core_265 anchor collapse and the sw_041 history backfill.

## 4. Verification

- [x] 4.1 Real-Postgres migration round trip, two-turn resume, connector, ingest, and history tests.
