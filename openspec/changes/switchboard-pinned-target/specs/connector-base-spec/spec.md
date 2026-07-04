## MODIFIED Requirements

### Requirement: ingest.v1 Envelope Schema
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
- **WHEN** `control.ingestion_tier` is `"full"` (Tier 1)
- **THEN** `payload.raw` must be a non-None dict containing the complete provider payload
- **WHEN** `control.ingestion_tier` is `"metadata"` (Tier 2)
- **THEN** `payload.raw` must be None and `payload.normalized_text` contains only the subject line or summary

#### Scenario: Dashboard channel exemption from discretion
- **WHEN** a message is ingested with `source.channel = "dashboard"`
- **THEN** the message SHALL bypass discretion evaluation entirely (operator messages are always intentional)
- **AND** the message proceeds directly to Switchboard classification/routing

### Requirement: Triage Integration

Connector-side and server-side ingestion rules SHALL gate ingestion and early routing decisions before LLM classification. Connector-scoped rules (`block` action) SHALL be evaluated at the connector. Global rules (all other actions) SHALL be evaluated post-ingest by the Switchboard.

An envelope's `control.pinned_target`, when present, SHALL take precedence over thread-affinity lookup and global ingestion-rule evaluation: the Switchboard SHALL produce a deterministic `route_to` triage decision to that butler without evaluating rules or invoking LLM classification. The pinned target SHALL be validated against the live, routable butler registry (the same candidate set used for LLM-classification routing: registered, `butler`-typed, `eligibility_state = 'active'`). An envelope naming an unknown, non-butler, or non-routable target SHALL be rejected at the ingest boundary (the envelope is not accepted) rather than silently falling through to classification or being misrouted.

#### Scenario: Pinned target routes deterministically
- **WHEN** an envelope is ingested with `control.pinned_target` set to a registered, routable butler name
- **THEN** the Switchboard produces a `route_to` triage decision targeting that butler
- **AND** thread-affinity lookup and global ingestion-rule evaluation are not performed
- **AND** the message is routed without LLM classification

#### Scenario: Unknown pinned target is rejected
- **WHEN** an envelope is ingested with `control.pinned_target` set to a name that is not a registered, routable butler
- **THEN** the ingest submission is rejected with a validation error
- **AND** no `message_inbox` or `public.ingestion_events` row is created for the submission

#### Scenario: Absent pinned target preserves existing behavior
- **WHEN** an envelope is ingested without `control.pinned_target` (or with it unset)
- **THEN** routing proceeds exactly as before this change: thread-affinity lookup (email only), then global ingestion-rule evaluation, then LLM classification fallback

#### Scenario: Thread affinity lookup (email only)
- **WHEN** an email message is ingested with a thread_id and no `pinned_target`
- **THEN** Switchboard checks thread affinity BEFORE evaluating global ingestion rules

#### Scenario: Deterministic rule evaluation
- **WHEN** a message passes connector-scoped evaluation and is accepted by the Switchboard, and no `pinned_target` was set
- **THEN** global ingestion rules are evaluated in priority order; the first match determines routing/action

#### Scenario: Ingestion tier classification
- **WHEN** no global ingestion rule matches (pass_through) and no `pinned_target` was set
- **THEN** the message proceeds to LLM classification
