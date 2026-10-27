## MODIFIED Requirements

### Requirement: Conversation History for Routing Context

The pipeline SHALL load channel-appropriate conversation history to improve LLM routing accuracy.

#### Scenario: Realtime messaging history
- **WHEN** the source channel is `telegram`, `whatsapp`, `slack`, or `discord`
- **THEN** recent conversation history is loaded (max 15-minute window, max 30 messages) for routing context
- **AND** realtime history SHALL select `message_inbox` rows only by `request_context ->> 'external_conversation_id'`, never by a per-message reply target or `source_thread_identity`

#### Scenario: Consecutive messages in one chat share history
- **WHEN** two Telegram messages from the same chat arrive with distinct reply targets
- **THEN** the second message's realtime history SHALL include the first, because both carry the same `external_conversation_id`

#### Scenario: Outbound rows join their conversation's history
- **WHEN** a delivered outbound message is written to `message_inbox` for history
- **THEN** its request context SHALL carry `external_conversation_id`: the notify request context's value when present, otherwise the key derived from the delivery target (for Telegram, `telegram:<chat_id>` of the chat the message was delivered to; for WhatsApp, `whatsapp:<chat_jid>`, including proactive sends)

#### Scenario: Pre-split history remains readable
- **WHEN** `message_inbox` holds rows written before connectors split conversation identity from reply targets
- **THEN** a one-time migration SHALL have backfilled their `external_conversation_id` with the key the connector emits today (Telegram `telegram:<chat_id>` from the legacy `<chat_id>` or `<chat_id>:<message_id>` value, WhatsApp `whatsapp:<chat_jid>`, every other channel verbatim)
- **AND** the stored `raw_payload.event` of rows still awaiting buffer recovery for the Telegram bot, Telegram user-client, and WhatsApp user-client channels SHALL carry both split fields

#### Scenario: Email thread history
- **WHEN** the source channel is `email`
- **THEN** the full email chain is loaded (max 50,000 tokens, newest messages preserved) for routing context

#### Scenario: No history for API/MCP channels
- **WHEN** the source channel is `api` or `mcp`
- **THEN** no conversation history is loaded
