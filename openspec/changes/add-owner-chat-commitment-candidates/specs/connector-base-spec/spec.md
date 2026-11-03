## ADDED Requirements

### Requirement: Per-Message Authorship and Forwarding Facts in Conversation History

The `whatsapp_user_client` and `telegram_user_client` connectors SHALL put two explicit boolean
transport facts on every `payload.raw.conversation_history` entry they emit: `is_from_me`, copied
from the provider's own outgoing-message flag (the bridge's whatsmeow `IsFromMe`, Telethon's
`Message.out`), and `forwarded`, copied from the provider's forward marker (the bridge's
`content.is_forwarded`, Telethon's `Message.fwd_from`). When the provider flag is unavailable the
connector SHALL omit the fact rather than guess it. An entry's `text` SHALL contain only that
message's own content; an earlier message it replies to SHALL be referenced by `reply_to`, never
inlined. These facts are transport representation only: the connector SHALL NOT detect
commitments, resolve counterparties, or write any butler schema because of them.

ID: REQ-connector-base-spec-013
Source: RFC 0038 D5 (owner words count only on connector-marked spans); architecture.md anti-pattern (connectors transport, never classify); bu-jv1moa.11 surface map
Scope: v1-reserved

#### Scenario: An owner-sent WhatsApp message carries both facts

- **WHEN** the WhatsApp bridge delivers a message with `is_from_me` true and no forward marker
- **THEN** its conversation history entry carries `is_from_me: true` and `forwarded: false`

#### Scenario: A forwarded Telegram message is marked

- **WHEN** the owner forwards someone else's message into a private Telegram chat
- **THEN** its conversation history entry carries `is_from_me: true` and `forwarded: true`

#### Scenario: A missing provider flag is omitted, not guessed

- **WHEN** a provider event lacks the outgoing or forward flag
- **THEN** the entry omits the corresponding fact, and consumers that require it treat the entry
  as not qualifying

#### Scenario: A reply does not inline the quoted message

- **WHEN** the owner replies to a counterparty's message
- **THEN** the entry's `text` is the owner's reply only and `reply_to` names the quoted message
