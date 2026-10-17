# Draft candidate: private rolling-summary custody

Status: NOT ADOPTED. Future additions require exact review/adoption and owning integrations. Full current requirements and prior scenarios are retained.

## MODIFIED Requirements

### Requirement: ingest.v1 Envelope Contract
The `ingest.v1` envelope SHALL be the canonical format for all messages entering the butler ecosystem. It SHALL conform to the IngestEnvelopeV1 schema with five required sub-sections validated at the point of entry.

After independent exact adoption and all owned custody integrations, for a private-summary turn only, the original source envelope and persisted normalized_text/raw_payload SHALL exclude private derivative/provenance. The generated overlay is not original provider content. Source/current message and safe raw-tail/omission view retain the existing schema; no new public field/general summary tool is authorized. Protected attempt binding stays outside exported envelope/request/trace fields. Owning ingress/dedupe integration establishes accepted-turn identity before resolution; unknown origin/binding uses marked raw tail. Other channels/media tiers/raw provider preservation/pin authority are unchanged.

ID: REQ-connector-base-spec-006
Source: connector-base-spec § ingest.v1 Envelope Contract; private summary custody R1; RFC 0003/0007 unchanged wire/source identity
Scope: proposed-owner-gated


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

#### Scenario: Summary-enabled envelope preserves source without derivative

- **WHEN** an authorized dashboard turn has valid private history
- **THEN** ingest.v1 current/source data are preserved while derivative/provenance are excluded from all durable envelope copies
- **AND** private history is resolved only at an eligible invocation, never from caller fields

#### Scenario: Duplicate ingress cannot capture a different private context

- **WHEN** duplicate or ambiguous acceptance reaches ingest for the same dashboard message
- **THEN** existing request/message identity and dedupe remain authoritative
- **AND** trusted acceptance admits the private attempt; no duplicate row/turn or same-attempt rebinding is created
