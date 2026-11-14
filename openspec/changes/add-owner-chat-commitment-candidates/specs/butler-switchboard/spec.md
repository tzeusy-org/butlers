## ADDED Requirements

### Requirement: Owner-Chat Span Projection

The Switchboard SHALL provide one fixed, read-only, deterministic projection over its own
`message_inbox` that returns owner-authored messages from one-to-one user-client chats, served
only on the authenticated service-plane endpoint bound to the Relationship daemon, and never
LLM-presentable. A returned span SHALL satisfy all of: source channel `whatsapp_user_client` or
`telegram_user_client`; stored `chat_type` private and `participant_count` at most 2; entry
`is_from_me` explicitly true and `forwarded` explicitly false (REQ-connector-base-spec-013); for
WhatsApp, entry `from_history` explicitly false, and for Telegram, entry `via_bot` explicitly
false; entry `is_new` true; the entry's `sender_identity` equal to the envelope's stored owner sender identity
whenever that identity is present; and a source event time no more than 14 days old. Each span
SHALL carry the source channel, endpoint identity, chat identity, the single non-owner
counterparty channel identity, the provider message id, the source event time, the ingestion
event id and the entry's text. The counterparty SHALL be the only identity in the row other
than the entry's author, drawn from the row's senders and history entries and, for a WhatsApp
private chat, from the stored `payload.raw.peer_identity` unless it equals the author or the
stored owner sender identity; zero such identities, or several (a peer that disagrees with
history included), SHALL yield no span. Spans are ordered by a stable `(received_at, inbox id)` cursor that the
caller supplies and the projection returns. Because `received_at` is assigned by the
application before the inbox insert commits, the projection SHALL exclude every row whose
`received_at` is less than 60 seconds before the database's current time, so a slow ingest
transaction commits before the cursor can pass its position. The projection SHALL take no other argument, SHALL
write nothing, and SHALL report unavailability as an error, never as an empty complete page.
Only batched `conversation_history` envelopes carry history entries. Single-message user-client
envelopes (Telegram backfill, ingest retry and the fallback when a chat id cannot be read) carry
none, so the projection SHALL NOT return their messages in S1; that coverage gap is explicit, not
a qualifying rule. The same holds for these cases, which the projection SHALL exclude rather than
guess:

- A WhatsApp history-sync replay can carry text that any linked device sent, a past bridge
  device's sends included. Live delivery never returns the bridge's own sends to it, because
  whatsmeow does not encrypt a send for its own device and dispatches no local event for it.
  Only an entry the bridge marked live therefore counts.
- A Telegram message sent via an inline bot is posted under the owner's account but written by
  the bot.

One case stays indistinguishable from owner typing and is accepted: a Telegram copy sent without
its author ("forward without author") carries no forward header.

ID: REQ-butler-switchboard-011
Source: about/heart-and-soul/vision.md non-negotiable 3 (cross-domain access MCP-only through the control plane); RFC 0038 D2 and D7; bu-jv1moa.11; bu-jv1moa.100
Scope: v1-reserved

#### Scenario: Only qualifying owner entries are returned

- **WHEN** a stored WhatsApp batch for a private chat holds an owner entry, a counterparty entry
  and a forwarded owner entry
- **THEN** the projection returns only the non-forwarded owner entry, with the counterparty's
  channel identity and the provider message id

#### Scenario: Group chats and inconsistent authorship are excluded

- **WHEN** an entry comes from a group chat, or claims `is_from_me: true` while its sender
  identity differs from the stored owner sender identity
- **THEN** the projection does not return it

#### Scenario: An owner-only WhatsApp batch names its counterparty from the peer fact

- **WHEN** a stored WhatsApp batch for a private chat holds only the owner's entries and its
  `payload.raw.peer_identity` is a non-owner identity
- **THEN** the projection returns the qualifying owner entries with that peer as the counterparty

#### Scenario: Without a usable peer fact the counterparty is not guessed

- **WHEN** an owner-only WhatsApp batch carries no `peer_identity` (a self-chat), or one equal to
  the owner, or one that disagrees with the history's non-owner sender, or the chat is a group
- **THEN** the projection returns no span from it

#### Scenario: Text the owner may not have typed is excluded

- **WHEN** a WhatsApp entry is a history-sync replay or carries no live marker, or a Telegram
  entry was sent via an inline bot or carries no `via_bot` fact
- **THEN** the projection does not return it

#### Scenario: Any other caller is refused

- **WHEN** a model session, connector or daemon other than the bound Relationship identity calls
  the projection
- **THEN** the call is refused before any row is read

#### Scenario: A late-committing ingest row is not skipped

- **WHEN** an ingest transaction assigns `received_at` T, a second ingest with a later
  `received_at` commits first, and the first commits 20 seconds after T
- **THEN** no page returned before T plus 60 seconds includes either row, and the next page
  after that includes both in cursor order

#### Scenario: The cursor resumes without gaps

- **WHEN** the caller passes the cursor returned with the previous page
- **THEN** the next page starts strictly after it, and no qualifying entry committed before the
  previous page's last row is skipped
