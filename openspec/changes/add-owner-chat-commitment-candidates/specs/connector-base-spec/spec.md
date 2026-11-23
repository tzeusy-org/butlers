## ADDED Requirements

### Requirement: Per-Message Authorship, Forwarding and Private-Peer Facts in Conversation History

The `whatsapp_user_client` and `telegram_user_client` connectors SHALL put two explicit boolean
transport facts on every `payload.raw.conversation_history` entry they emit: `is_from_me`, copied
from the provider's own outgoing-message flag (the bridge's whatsmeow `IsFromMe`, Telethon's
`Message.out`), and `forwarded`, copied from the provider's forward marker (the bridge's
`content.is_forwarded`, Telethon's `Message.fwd_from`). The WhatsApp connector SHALL also copy
`from_history`, the bridge's `raw.from_history` (true for a history-sync replay, false for live
delivery), and the Telegram connector SHALL also copy `via_bot`, whether Telethon's
`Message.via_bot_id` is set. The bridge SHALL mark a forward on every message kind whose
protobuf carries a context, including location, contact, poll and group-invite messages. When the provider flag is unavailable the
connector SHALL omit the fact rather than guess it. The `whatsapp_user_client` SHALL also put the
normalized identity of the other party on `payload.raw.peer_identity` for a private chat only,
derived from the chat JID by the same normalization as `sender_identity` (a mapped LID becomes its
phone identity); only a phone or linked-identity chat is a private peer; a group, channel, broadcast or other
chat, and a self-chat whose peer equals the owner, SHALL carry none, and a chat JID that does not
normalize SHALL omit it rather than guess it. An entry's `text` SHALL contain only that
message's own content; an earlier message it replies to SHALL be referenced by `reply_to`, never
inlined. These facts are transport representation only: the connector SHALL NOT detect
commitments, resolve counterparties, or write any butler schema because of them.

ID: REQ-connector-base-spec-013
Source: bu-jv1moa.93 (private-chat peer); RFC 0038 D5 (owner words count only on connector-marked spans); architecture.md anti-pattern (connectors transport, never classify); bu-jv1moa.11 surface map
Scope: v1-reserved

#### Scenario: An owner-sent WhatsApp message carries both facts

- **WHEN** the WhatsApp bridge delivers a message with `is_from_me` true and no forward marker
- **THEN** its conversation history entry carries `is_from_me: true` and `forwarded: false`

#### Scenario: A forwarded Telegram message is marked

- **WHEN** the owner forwards someone else's message into a private Telegram chat
- **THEN** its conversation history entry carries `is_from_me: true` and `forwarded: true`

#### Scenario: A WhatsApp history replay is marked

- **WHEN** the bridge replays a message from WhatsApp history sync
- **THEN** its conversation history entry carries `from_history: true`, and a live message
  carries `from_history: false`

#### Scenario: A missing provider flag is omitted, not guessed

- **WHEN** a provider event lacks the outgoing or forward flag
- **THEN** the entry omits the corresponding fact, and consumers that require it treat the entry
  as not qualifying

#### Scenario: A reply does not inline the quoted message

- **WHEN** the owner replies to a counterparty's message
- **THEN** the entry's `text` is the owner's reply only and `reply_to` names the quoted message

#### Scenario: A private chat names its peer even when only the owner spoke

- **WHEN** a WhatsApp flush from a private chat holds only owner messages
- **THEN** `payload.raw.peer_identity` carries the chat's normalized peer identity
- **AND** a consumer deriving the counterparty (bu-jv1moa.11) may use it when no history entry
  carries a non-owner sender, so an owner-only flush is no longer excluded for lack of a
  counterparty

#### Scenario: A group carries no peer

- **WHEN** the chat is a group
- **THEN** `payload.raw.peer_identity` is absent

#### Scenario: A malformed chat JID is omitted

- **WHEN** the chat JID is empty or does not normalize to an identity
- **THEN** `payload.raw.peer_identity` is absent and nothing is guessed

#### Scenario: A self-chat carries no peer

- **WHEN** the owner messages their own chat, so the peer would equal `owner_sender_id`
- **THEN** `payload.raw.peer_identity` is absent
