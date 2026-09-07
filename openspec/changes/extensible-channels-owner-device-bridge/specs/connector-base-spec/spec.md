## MODIFIED Requirements

### Requirement: ingest.v1 Envelope Schema
The `ingest.v1` envelope SHALL be the canonical format for all messages entering the butler ecosystem. It SHALL conform to the IngestEnvelopeV1 schema with five required sub-sections validated at the point of entry. Before catalog enforcement cutover, source tokens and pairs SHALL retain the closed enum and static Pydantic validation contract. After that cutover, source tokens SHALL use the bounded syntax below and Switchboard's catalog SHALL become the sole semantic pair authority before deduplication or persistence; every other envelope field and validation scenario remains unchanged.

ID: REQ-connector-base-spec-002
Source: RFC 0033 §Existing Contracts and Precedence and §D3 (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Top-level envelope structure
- **WHEN** a connector constructs an ingest envelope
- **THEN** it contains: `schema_version` (must be `"ingest.v1"`), `source` (IngestSourceV1), `event` (IngestEventV1), `sender` (IngestSenderV1), `payload` (IngestPayloadV1), `control` (IngestControlV1)

#### Scenario: Source identity (IngestSourceV1)
- **WHEN** `source` is populated
- **THEN** before catalog enforcement cutover, `channel` is a `SourceChannel` enum value and `provider` is a `SourceProvider` enum value; after cutover, both are canonical strings matching lowercase ASCII `[a-z][a-z0-9_]{0,63}`
- **AND** `endpoint_identity` remains a non-empty string uniquely identifying the connector instance in both stages
- **AND** before cutover, `channel` is a `SourceChannel` enum value (`telegram`, `slack`, `email`, `api`, `mcp`, `voice`, `google_calendar`, `dashboard`, `owntracks`, `home_assistant`, `google_drive`), `provider` is a `SourceProvider` enum value (`telegram`, `slack`, `gmail`, `imap`, `internal`, `live-listener`, `google_calendar`, `owntracks`, `home_assistant`, `google_drive`), and `endpoint_identity` is a non-empty string uniquely identifying the connector instance (e.g., `"gmail:user:alice@gmail.com"`, `"telegram:bot:mybot"`, `"live-listener:mic:kitchen"`, `"google_calendar:user:work@gmail.com"`, `"dashboard:web:{conversation_id}"`, `"owntracks:ab"`, `"home_assistant:ha-host:8123"`, `"google_drive:user:alice@gmail.com"`)

#### Scenario: Channel-provider pair validation
- **WHEN** `source.channel` and `source.provider` are set
- **THEN** before catalog enforcement cutover, the closed `SourceChannel` and `SourceProvider` enums and static Pydantic pair matrix SHALL remain authoritative; after cutover, Pydantic SHALL enforce bounded token syntax and Switchboard SHALL enforce the exact enabled pair from `public.source_channel_catalog` before deduplication or persistence
- **AND** a syntactically valid unknown, disabled, or mismatched pair SHALL fail catalog validation rather than Pydantic enum validation
- **AND** before cutover, valid pairings are enforced: `telegram`/`telegram`, `email`/`gmail`, `email`/`imap`, `api`/`internal`, `mcp`/`internal`, `voice`/`live-listener`, `google_calendar`/`google_calendar`, `dashboard`/`internal`, `owntracks`/`owntracks`, `home_assistant`/`home_assistant`, `google_drive`/`google_drive`
- **AND** before cutover, invalid pairings fail Pydantic validation

#### Scenario: Event metadata (IngestEventV1)
- **WHEN** `event` is populated
- **THEN** `external_event_id` is a non-empty string (the provider's stable event ID, required for deduplication), `external_thread_id` is an optional non-empty string (email thread ID, Telegram chat ID), and `observed_at` is a timezone-aware datetime (RFC3339, when the connector observed the event)

#### Scenario: Sender identity (IngestSenderV1)
- **WHEN** `sender` is populated
- **THEN** `identity` is a non-empty string representing the sender (email address, Telegram user ID, etc.)

#### Scenario: Payload with tiered content (IngestPayloadV1)
- **WHEN** `payload` is populated
- **THEN** `raw` is the full provider payload dict (required non-None for Tier 1 "full", must be None for Tier 2 "metadata"), `normalized_text` is a non-empty string (the best available human-readable text), and `attachments` is an optional tuple of `IngestAttachment` records

#### Scenario: Attachment metadata (IngestAttachment)
- **WHEN** an attachment is included
- **THEN** it contains: `media_type` (MIME type string), `storage_ref` (storage reference for lazy fetch), `size_bytes` (uncompressed size), `filename` (optional), `width` and `height` (optional, for images)

#### Scenario: Control directives (IngestControlV1)
- **WHEN** `control` is populated
- **THEN** `idempotency_key` is an optional explicit dedup key (overrides default computation), `trace_context` is a dict of tracing metadata, `policy_tier` is a `PolicyTier` enum (`default`, `interactive`, `high_priority`) for queue ordering, `ingestion_tier` is an `IngestionTier` enum (`full` for Tier 1, `metadata` for Tier 2), and `pinned_target` is an optional non-empty string naming the butler this envelope SHALL be routed to

#### Scenario: Tier-dependent payload validation
- **WHEN** `control.ingestion_tier` is evaluated
- **THEN** `"full"` (Tier 1) requires `payload.raw` to be a non-None dict containing the complete provider payload
- **AND** `"metadata"` (Tier 2) requires `payload.raw` to be None and `payload.normalized_text` to contain only the subject line or summary
- Before and after cutover, **WHEN** `control.ingestion_tier` is `"full"` (Tier 1)
- Before and after cutover, **THEN** `payload.raw` must be a non-None dict containing the complete provider payload
- Before and after cutover, **WHEN** `control.ingestion_tier` is `"metadata"` (Tier 2)
- Before and after cutover, **THEN** `payload.raw` must be None and `payload.normalized_text` contains only the subject line or summary

#### Scenario: Dashboard channel exemption from discretion
- **WHEN** a message is ingested with `source.channel = "dashboard"`
- **THEN** the message SHALL bypass discretion evaluation entirely (operator messages are always intentional)
- **AND** the message proceeds directly to Switchboard classification/routing
