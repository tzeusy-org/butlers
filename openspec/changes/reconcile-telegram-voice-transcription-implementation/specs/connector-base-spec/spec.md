# Connector Base Spec — Delta

## ADDED Requirements

### Requirement: Transcription Outcome Payload Field

The `ingest.v1` envelope's `payload` SHALL support an optional `transcription` object that a
transcription-capable connector populates to record the outcome of its own speech-to-text attempt.
This field is independent of, and never derived from, a provider's own raw payload — it exists only
for outcome metadata the provider itself does not report. The existing nonvoice envelope scenarios remain unchanged; the separately defined strict operational-failure exception is the only expansion of empty-text admission, and `payload.transcription` is absent for
every message no connector-side transcription attempt was made against.

#### Scenario: Field absent by default

- **WHEN** an `ingest.v1` envelope is constructed for a message no connector-side transcription
  attempt was evaluated against
- **THEN** `payload.transcription` is absent (or `None`), and existing consumers reading only
  `payload.raw`, `payload.normalized_text`, and `payload.attachments` are unaffected

#### Scenario: Field populated for an evaluated transcription attempt

- **WHEN** a connector evaluates a message for its own transcription capability (for example, the
  `telegram-voice-transcription` capability evaluating a Telegram `voice` message)
- **THEN** `payload.transcription` is populated with `source_media` (a string naming the media kind,
  e.g. `"voice"`), `status` (one of the closed outcomes defined by the accepted capability), `confidence` (finite float in `[0, 1]`, excluding bools, or
  `null`), `language` (BCP-47 string or `null`), and `stt_backend` (string or `null`)

#### Scenario: Field never carries audio bytes

- **WHEN** `payload.transcription` is populated
- **THEN** it contains only text/numeric/string outcome metadata — never raw or encoded audio bytes,
  and never a reference (storage or otherwise) to the original audio

### Requirement: Operational Voice Failure Admission
The payload SHALL support empty normalized_text without an attachment only for a strict operational voice-failure outcome. Switchboard SHALL validate this exception against authenticated registered connector/source identity, real owner sender resolution and accepted inbox lineage; it SHALL not treat operational error text as an owner report or accept caller-asserted actor/recipient/request identities. Unknown fields, malformed outcomes and arbitrary empty content SHALL remain refused. Validated transcription provenance SHALL survive inbox serialization and be copied into ordinary downstream request context, without granting identity or content authority.

#### Scenario: Bound voice-failure envelope
- **WHEN** an authenticated admitted owner voice source submits a valid operational failure with no transcript
- **THEN** the empty-text exception is admitted for deterministic failure handling, no audio attachment is minted, and no owner speech or fact is fabricated

#### Scenario: Forged or malformed empty event
- **WHEN** an arbitrary caller or malformed payload claims voice failure
- **THEN** admission refuses it and no outbound effect occurs

#### Scenario: Successful transcript context
- **WHEN** a validated successful transcript is admitted
- **THEN** source_media voice and its validated outcome reach normal downstream context with existing routing/source authority intact

## MODIFIED Requirements

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
- **AND** `normalized_text` MAY be the empty string only for a captionless media message, per the Media Normalization Obligation below, or for the separately authenticated operational voice-failure exception defined by the accepted Telegram voice capability

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

#### Scenario: Accepted operational voice failure
- **WHEN** the accepted capability supplies a strictly validated failure from an authenticated admitted owner voice source
- **THEN** empty normalized_text is permitted without an audio attachment solely for deterministic failure handling; arbitrary empty events remain refused

### Requirement: Media Normalization Obligation

For the exact reviewed-and-owner-accepted Telegram voice capability only, bounded private audio SHALL instead be transcribed or disposed without attachment persistence. Its admitted operational-failure event SHALL use the strict empty-text exception; all other media and existing scenarios retain their attachment obligation.

A connector that receives a message carrying non-text content (an image, document, or other media attachment, as opposed to plain text) MUST materialize an `IngestAttachment` for that content and MUST NOT synthesize a media-type descriptor (e.g. `"Photo"`, `"[Photo]"`, `"[Document]"`) into `normalized_text` as a substitute for the real content. `normalized_text` for such a message is the message's caption verbatim, or the empty string when there is no caption — never a fabricated placeholder that would let an uncaptioned attachment masquerade as text content a downstream consumer already understood.

#### Scenario: Non-text content is materialized, not described
- **WHEN** a connector receives a message whose content is non-text (e.g. a Telegram photo or document)
- **THEN** the connector fetches the media bytes and stores them via BlobStore, producing an `IngestAttachment` with a real `storage_ref` on success
- **AND** `payload.normalized_text` is set to the message's caption (or `""` when the message has no caption) — never a synthesized descriptor string

#### Scenario: Materialization failure preserves the message
- **WHEN** a connector's attempt to fetch or store the media bytes fails (source API error, expired reference, blob store unavailable)
- **THEN** the connector still submits the envelope with `payload.normalized_text` set to the caption (or `""`), and an `IngestAttachment` whose `storage_ref` is `None` and `size_bytes` is `0`
- **AND** the connector records the failure in `connectors.filtered_events` with `status='error'` for operator visibility
- **AND** the message is NOT dropped solely because attachment materialization failed — a fetch failure degrades the attachment, it does not withhold the message

#### Scenario: Idempotent materialization across replay
- **WHEN** the same source message is processed more than once (retry, at-least-once redelivery, or an operator-triggered replay)
- **THEN** the connector puts at most one blob per media id — a prior successful materialization for the same `(endpoint_identity, external_message_id, media_id)` is detected and its existing `storage_ref` is reused rather than re-fetching and re-storing the bytes

#### Scenario: Private voice transport exception
- **WHEN** a voice message is handled by the accepted Telegram voice capability
- **THEN** no voice audio attachment or storage reference is minted; successful real text or the strict authenticated operational failure follows that capability, and unrelated media behavior is unchanged

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

#### Scenario: Validated voice provenance
- **WHEN** accepted payload transcription provenance exists
- **THEN** its strictly validated source_media and outcome are copied into downstream context without replacing server-derived request, source, sender or reply identity

