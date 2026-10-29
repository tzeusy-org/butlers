## MODIFIED Requirements

### Requirement: ingest.v1 Field Mapping

The Gmail connector SHALL normalize every ingested Gmail message to the
`ingest.v1` envelope using exactly the field mapping defined below.

#### Scenario: Gmail field mapping
- **WHEN** a Gmail email is normalized to `ingest.v1`
- **THEN** the mapping is:
  - `source.channel` = `"email"`
  - `source.provider` = `"gmail"` (must be `gmail`, not `imap`)
  - `source.endpoint_identity` = `"gmail:user:<email_address>"`
  - `event.external_event_id` = the RFC822 `Message-ID` header value (falls back to the Gmail message ID when the header is absent)
  - `event.external_conversation_id` = Gmail `threadId`
  - `event.reply_target_ref` = Gmail `threadId`
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = normalized sender address from `From` header
  - `sender.display_name` = the raw display-name part of the `From` header (e.g. `"John Doe"` from `"John Doe <john@example.com>"`), or `null` when the header carried no display name; stored verbatim (not normalized) so identity enrichment can use the real name instead of guessing one from the address local-part
  - `payload.raw` = full Gmail API message payload (Tier 1) or `null` (Tier 2)
  - `payload.normalized_text` = normalized subject + body text (Tier 1) or subject only (Tier 2)
  - `control.idempotency_key` = `"gmail:<endpoint_identity>:<message_id>"`

### Requirement: Email Metadata Storage for Tier 2

Tier 2 (metadata-only) emails SHALL be persisted in the canonical `switchboard.message_inbox` lifecycle table, tagged with `ingestion_tier='metadata'`. There is no
separate Tier 2 metadata table; `message_inbox` is the single source of
truth for accepted ingestion records across all tiers.

#### Scenario: Tier 2 metadata persistence
- **WHEN** a Tier 2 email is accepted
- **THEN** the connector submits a slim `ingest.v1` envelope with `payload.raw=null`,
  `payload.normalized_text=<subject only>`, and `control.ingestion_tier="metadata"`
- **AND** Switchboard persists a `message_inbox` row with `ingestion_tier='metadata'`,
  bypassing LLM classification; `raw_payload` retains the source endpoint identity,
  `external_event_id` (Gmail message ID), `external_conversation_id`,
  `reply_target_ref`, and sender identity

#### Scenario: Tier 2 metadata is queryable by tier
- **WHEN** Tier 2 records are queried
- **THEN** they are retrievable via the `ix_message_inbox_ingestion_tier_received_at`
  index on `(ingestion_tier, received_at DESC)`

#### Scenario: On-demand body retrieval
- **WHEN** a butler needs the full body of a Tier 2 email
- **THEN** it is fetched on demand from Gmail API by message ID
- **AND** fetching does not auto-promote to Tier 1
