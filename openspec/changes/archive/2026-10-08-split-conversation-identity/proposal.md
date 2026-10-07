# Split conversation identity from reply targets

## Why

Telegram bot ingress overloaded `event.external_thread_id` as `<chat_id>:<message_id>`. Every
message therefore minted a new conversation anchor, the provider-resume ledger never matched, each
turn cold-started, and zero-message ghost conversations piled into the chat list (bu-7exe4.2). The
core_208 helper normalized that key inside the anchor upsert; the connector should instead emit a
stable conversation key, and every continuity consumer should select on it.

## What Changes

- `ingest.v1` events gain `external_conversation_id` (the continuity key) and `reply_target_ref`
  (the per-message reply or reaction target). `external_thread_id` stays accepted for producers
  that have not split; for them it is both keys. `telegram_bot` envelopes must carry both split
  fields.
- Telegram bot, Telegram user-client, WhatsApp user-client, and Gmail emit the split fields.
- The conversation anchor upserts on a new partial unique index over
  `(butler_name, source_channel, external_conversation_id)`; the core_208 normalization is removed.
- Migration core_263 collapses legacy per-message Telegram anchors reversibly; switchboard migration
  sw_041 backfills the key on historical `message_inbox` rows. Realtime history selects on it.
- Outbound history rows and filtered-event replay carry the key.
- Relationship `interaction_sync` groups chats on the key, so per-message reply targets do not
  split one chat into many.
- The dashboard envelope is unchanged.

## Impact

- Affected specs: `connector-telegram-bot`, `connector-telegram-user-client`,
  `telegram-user-client-conversation-history`, `connector-gmail`, `connector-base-spec`,
  `module-pipeline`, `connector-filtered-events`, `dashboard-conversations`,
  `passive-interaction-sync`.
- Affected code: connectors, Switchboard ingest and delivery, routing anchor, pipeline history,
  filtered-event replay, Relationship `interaction_sync`, core_263 and sw_041 migrations.
- Rollout: stop every conversation-anchor writer, migrate, then start all writers on the new image
  (bu-psarp owner decision, 2026-10-08).
