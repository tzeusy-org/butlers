## ADDED Requirements

### Requirement: Owner-Chat Span Projection

The Switchboard SHALL provide one fixed, read-only, deterministic projection over its own
`message_inbox` that returns owner-authored messages from one-to-one user-client chats, served
only on the authenticated service-plane endpoint bound to the Relationship daemon, and never
LLM-presentable. A returned span SHALL satisfy all of: source channel `whatsapp_user_client` or
`telegram_user_client`; stored `chat_type` private and `participant_count` at most 2; entry
`is_from_me` explicitly true and `forwarded` explicitly false (REQ-connector-base-spec-013); entry
`is_new` true; the entry's `sender_identity` equal to the envelope's stored owner sender identity
whenever that identity is present; and a source event time no more than 14 days old. Each span
SHALL carry the source channel, endpoint identity, chat identity, the single non-owner
counterparty channel identity, the provider message id, the source event time, the ingestion
event id and the entry's text, ordered by a stable `(received_at, inbox id)` cursor that the
caller supplies and the projection returns. Because `received_at` is assigned by the
application before the inbox insert commits, the projection SHALL exclude every row whose
`received_at` is less than 60 seconds before the database's current time, so a slow ingest
transaction commits before the cursor can pass its position. The projection SHALL take no other argument, SHALL
write nothing, and SHALL report unavailability as an error, never as an empty complete page.

ID: REQ-butler-switchboard-011
Source: about/heart-and-soul/vision.md non-negotiable 3 (cross-domain access MCP-only through the control plane); RFC 0038 D2 and D7; bu-jv1moa.11
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
