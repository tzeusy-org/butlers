# Connector Owner Direction Evidence

## ADDED Requirements

### Requirement: Owner-sent messages are reported only through the batch envelope's owner sender

A user-client connector that observes the owner's own messages (`telegram_user_client`,
`whatsapp_user_client`) SHALL report the owner's identity for a batch through
`sender.owner_sender_id`, so the Switchboard persists it as `request_context.owner_sender_identity`.
WhatsApp SHALL derive ownership from the bridge's `is_from_me` flag, not from a phone-number
comparison. A connector that cannot tell which message is the owner's SHALL omit
`owner_sender_id`; it SHALL NOT guess. These are the only connector-reported outbound-direction signals
that relationship turn evidence may use.

ID: REQ-connector-owner-direction-evidence-001
Source: bu-h40h2b.14 Slice A (eligible outbound sources); [Observed] src/butlers/connectors/whatsapp_user_client.py `_project_batch_senders` / owner detection via `raw.is_from_me`; passive-interaction-sync "Batch envelopes carry per-sender identities"
Scope: v1-reserved

#### Scenario: WhatsApp owner message

- **WHEN** a WhatsApp batch contains a message the bridge tags `is_from_me`
- **THEN** the envelope's `sender.owner_sender_id` names that sender

#### Scenario: Ownership unknown

- **WHEN** a connector cannot determine which sender is the owner
- **THEN** `owner_sender_id` is omitted

### Requirement: Gmail sent-mail awareness stays metadata for reply policy

The Gmail connector's `in:sent` listing SHALL remain bounded by `GMAIL_SENT_LOOKBACK_DAYS` and
`GMAIL_SENT_MAX_MESSAGES`, SHALL read only the `Message-ID` header (metadata format), and SHALL feed only
the reply-policy tier assignment. It SHALL NOT retain recipients, subject, date or thread, SHALL NOT be
persisted, and SHALL NOT be treated as outbound interaction evidence. Widening it to carry such fields
is a new outbound-data collection surface and requires its own owner-approved connector contract.

ID: REQ-connector-owner-direction-evidence-002
Source: bu-h40h2b.14 Slice B and acceptance 2; [Observed] src/butlers/connectors/gmail.py `_fetch_sent_message_ids` (query `in:sent newer_than:{lookback_days}d`, `format=metadata`, `metadataHeaders=Message-ID`) feeding `PolicyTierAssigner.sent_message_ids`
Scope: v1-mandatory

#### Scenario: Only the Message-ID header is read

- **WHEN** the connector refreshes its sent set
- **THEN** each sent message is read with `format=metadata` and the `Message-ID` header only

#### Scenario: Sent set is not interaction evidence

- **WHEN** the relationship projection looks for outbound evidence
- **THEN** the Gmail sent set contributes nothing

### Requirement: Owner outbound point events stay counterpart-less

`connectors.owner_outbound_events` SHALL continue to carry only a timestamp and channel, with a hashed
deduplication key, and no content or counterpart identity. Because it cannot say to whom the owner wrote,
it SHALL NOT be used as turn evidence, and no counterpart column SHALL be added to serve this contract.

ID: REQ-connector-owner-direction-evidence-003
Source: bu-whhll.8 privacy contract in src/butlers/connectors/owner_outbound_events.py; bu-h40h2b.14 Slice A
Scope: v1-mandatory

Known gap: for `telegram_user_client` the unkeyed SHA-256 deduplication key lets the counterpart be
recovered by enumeration (peer chat id, sequential message ids, narrowed by `occurred_at`;
bu-szrvwu), so the table is not counterpart-less in practice for Telegram until that key is a keyed
HMAC. That is a further reason it is never read as turn evidence.

#### Scenario: Point events are not turn evidence

- **WHEN** the relationship projection looks for outbound evidence for a person
- **THEN** `connectors.owner_outbound_events` is not read

#### Scenario: No counterpart column

- **WHEN** this contract is implemented
- **THEN** `connectors.owner_outbound_events` gains no counterpart or content column

## Source References

- Non-Negotiable Rule 7 (transport is connector responsibility)
- RFC 0013 (interaction scoring), RFC 0038 (ingest envelopes remain)
