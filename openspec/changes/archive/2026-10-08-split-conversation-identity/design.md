# Design

## Identity

One module, `butlers.conversation_identity`, builds every Telegram and WhatsApp key so live ingress,
filtered-event replay, and outbound history agree: `telegram:<chat_id>`, suffixed
`:topic:<message_thread_id>` only when Telegram marks the message `is_topic_message`, and
`whatsapp:<chat_jid>`. One resolver, `event_conversation_identity`, applies the fallback for
producers that send only `external_thread_id`.

## Anchor

The helper keeps the connection-bound, advisory-locked conflict recovery the core_208 work
introduced, keyed by the conversation identity, and writes the same value into the legacy
`source_thread_identity` column so both unique indexes stay congruent.

## Migration

core_263 runs its data transform on the first schema that installs the column, because core
revisions replay per schema against shared `public` tables. It snapshots every collapsed anchor,
moved message and turn link, and rewritten identity into private `core_263_*` tables. Downgrade,
on the last schema to leave, restores those rows, links, and identities without reverting other
post-upgrade survivor columns, then drops the column, index, and snapshots. A leftover snapshot
fails the next upgrade rather than being reused.

There is no mixed-version compatibility trigger. A schema-level alias could not make the core_208
helper's separate statements converge, and the owner chose a stop-the-world rollout instead.
