## REMOVED Requirements

### Requirement: ingest.v1 Field Mapping

Superseded by `Stable ingest.v1 Field Mapping` because the public envelope no
longer overloads `external_thread_id`.

## ADDED Requirements

### Requirement: Stable ingest.v1 Field Mapping

The Gmail connector SHALL normalize every ingested Gmail message to the
canonical `ingest.v1` envelope.

#### Scenario: Gmail field mapping

- **WHEN** a Gmail email is normalized to `ingest.v1`
- **THEN** `event.external_event_id` SHALL carry the RFC822 Message-ID (falling back to the Gmail message ID)
- **AND** both `event.external_conversation_id` and `event.reply_target_ref` SHALL carry the Gmail `threadId`
- **AND** source, sender, observed-at, tiered payload, and idempotency fields SHALL retain their canonical Gmail mappings
