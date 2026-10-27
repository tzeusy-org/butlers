## REMOVED Requirements

### Requirement: ingest.v1 Envelope Schema

**Reason**: The event block no longer has a single thread field. Superseded by
`ingest.v1 Envelope Contract`, which keeps every clause and adds the split
between `external_conversation_id` and `reply_target_ref`.

**Migration**: Read `ingest.v1 Envelope Contract`.

### Requirement: Request Context Assignment

**Reason**: `source_thread_identity` no longer derives from `external_thread_id`
for split producers. Superseded by `Request Context Construction`, which keeps
every clause and names the resolved conversation key and reply target.

**Migration**: Read `Request Context Construction`.

## ADDED Requirements

### Requirement: ingest.v1 Envelope Contract
The `ingest.v1` envelope SHALL be the canonical format for all messages entering the butler ecosystem. It SHALL conform to the IngestEnvelopeV1 schema with five required sub-sections validated at the point of entry.

#### Scenario: Top-level envelope structure
- **WHEN** a connector constructs an ingest envelope
- **THEN** it contains: `schema_version` (must be `"ingest.v1"`), `source` (IngestSourceV1), `event` (IngestEventV1), `sender` (IngestSenderV1), `payload` (IngestPayloadV1), `control` (IngestControlV1)

#### Scenario: Source identity (IngestSourceV1)
- **WHEN** `source` is populated
- **THEN** `channel` is a `SourceChannel` enum value (`telegram`, `slack`, `email`, `api`, `mcp`, `voice`, `google_calendar`, `dashboard`, `owntracks`, `home_assistant`, `google_drive`), `provider` is a `SourceProvider` enum value (`telegram`, `slack`, `gmail`, `imap`, `internal`, `live-listener`, `google_calendar`, `owntracks`, `home_assistant`, `google_drive`), and `endpoint_identity` is a non-empty string uniquely identifying the connector instance (e.g., `"gmail:user:alice@gmail.com"`, `"telegram:bot:mybot"`, `"live-listener:mic:kitchen"`, `"google_calendar:user:work@gmail.com"`, `"dashboard:web:{conversation_id}"`, `"owntracks:ab"`, `"home_assistant:ha-host:8123"`, `"google_drive:user:alice@gmail.com"`)

#### Scenario: Channel-provider pair validation
- **WHEN** `source.channel` and `source.provider` are set
- **THEN** valid pairings are enforced: `telegram`/`telegram`, `email`/`gmail`, `email`/`imap`, `api`/`internal`, `mcp`/`internal`, `voice`/`live-listener`, `google_calendar`/`google_calendar`, `dashboard`/`internal`, `owntracks`/`owntracks`, `home_assistant`/`home_assistant`, `google_drive`/`google_drive`
- **AND** invalid pairings fail Pydantic validation

#### Scenario: Event metadata (IngestEventV1)
- **WHEN** `event` is populated
- **THEN** `external_event_id` is a non-empty string (the provider's stable event ID, required for deduplication), `external_conversation_id` is an optional non-empty, channel-namespaced conversation key (for example `telegram:<chat_id>`, `whatsapp:<chat_jid>`, or a Gmail `threadId`), `reply_target_ref` is an optional non-empty per-message reply or reaction target (for example `<chat_id>:<message_id>`), `external_thread_id` is an optional non-empty string for producers that have not split conversation identity from reply targeting, and `observed_at` is a timezone-aware datetime (RFC3339, when the connector observed the event)
- **AND** for a producer that sends only `external_thread_id`, that value SHALL serve as both its conversation key and its reply target; a split field SHALL always take precedence over it
- **AND** a `telegram_bot` envelope SHALL carry both `external_conversation_id` and `reply_target_ref`, or fail validation

#### Scenario: Sender identity (IngestSenderV1)
- **WHEN** `sender` is populated
- **THEN** `identity` is a non-empty string representing the sender (email address, Telegram user ID, etc.)

#### Scenario: Payload with tiered content (IngestPayloadV1)
- **WHEN** `payload` is populated
- **THEN** `raw` is the full provider payload dict (required non-None for Tier 1 "full", must be None for Tier 2 "metadata"), `normalized_text` is the best available human-readable text and is non-empty whenever the message carries text, a caption, or any other author-supplied text content, and `attachments` is an optional tuple of `IngestAttachment` records
- **AND** `normalized_text` MAY be the empty string only for a captionless media message, per the Media Normalization Obligation below

#### Scenario: Attachment metadata (IngestAttachment)
- **WHEN** an attachment is included
- **THEN** it contains: `media_type` (MIME type string), `storage_ref` (storage reference for lazy fetch, `None` when materialization failed or has not yet occurred), `size_bytes` (uncompressed size; `0` when `storage_ref` is `None`, never `None` itself — the field is non-nullable), `filename` (optional), `width` and `height` (optional, for images)

#### Scenario: Control directives (IngestControlV1)
- **WHEN** `control` is populated
- **THEN** `idempotency_key` is an optional explicit dedup key (overrides default computation), `trace_context` is a dict of tracing metadata, `policy_tier` is a `PolicyTier` enum (`default`, `interactive`, `high_priority`) for queue ordering, `ingestion_tier` is an `IngestionTier` enum (`full` for Tier 1, `metadata` for Tier 2), and `pinned_target` is an optional non-empty string naming the butler this envelope SHALL be routed to

#### Scenario: Tier-dependent payload validation
- **WHEN** `control.ingestion_tier` is `"full"` (Tier 1)
- **THEN** `payload.raw` must be a non-None dict containing the complete provider payload
- **WHEN** `control.ingestion_tier` is `"metadata"` (Tier 2)
- **THEN** `payload.raw` must be None and `payload.normalized_text` contains only the subject line or summary

#### Scenario: Dashboard channel exemption from discretion
- **WHEN** a message is ingested with `source.channel = "dashboard"`
- **THEN** the message SHALL bypass discretion evaluation entirely (operator messages are always intentional)
- **AND** the message proceeds directly to Switchboard classification/routing

### Requirement: Request Context Construction
The Switchboard SHALL build an immutable request context from each accepted ingest envelope. This context SHALL travel with the message through classification, routing, and butler processing. The `request_id` SHALL be a UUID7 identifier.

#### Scenario: Request context fields
- **WHEN** a message is accepted for processing
- **THEN** the Switchboard assigns: `request_id` (UUID7, equals `public.ingestion_events.id`), `received_at` (server timestamp), `source_channel`, `source_endpoint_identity`, `source_sender_identity`, `external_conversation_id` (the resolved conversation key), `reply_target_ref` (the resolved reply target), `source_thread_identity` (the notify-facing name for the same reply target, never the conversation key), `idempotency_key`, `trace_context`, `ingestion_tier`, `dedupe_key`, `dedupe_strategy` (`"connector_api"`)
- **AND** if triage was evaluated: `triage_decision`, `triage_target`, `triage_rule_id`, `triage_rule_type`
- **AND** if the envelope carries group-chat metadata: `participant_count`, `chat_type`, and `interaction_eligible` (only when `false`)
- **AND** if the envelope is a batch covering several senders: `source_sender_identities` (a JSON array built from `sender.participants`) and, when the connector reports one, `owner_sender_identity` (from `sender.owner_sender_id`)
- **AND** both batch keys are omitted for single-sender envelopes, whose `source_sender_identity` already names the sender
- **AND** the `request_id` is passed through to the spawned butler session as both `session.request_id` and `session.ingestion_event_id`

#### Scenario: Batch envelopes preserve per-sender identity
- **WHEN** a connector flushes a buffered batch spanning several senders
- **THEN** `sender.identity` remains the collapsed sentinel `"multiple"`
- **AND** the per-sender identities MUST still reach `request_context` via `source_sender_identities`, because downstream consumers (notably passive interaction sync) resolve contacts from them and cannot resolve the sentinel
