## MODIFIED Requirements

### Requirement: ingest.v1 Envelope Contract
The `ingest.v1` envelope SHALL be the canonical format for all messages entering the butler ecosystem. It SHALL conform to the IngestEnvelopeV1 schema with five required sub-sections validated at the point of entry. No envelope field SHALL name a target butler; an accepted envelope is goal input, not a routing request.

Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

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
- **THEN** `idempotency_key` is an optional explicit dedup key (overrides default computation), `trace_context` is a dict of tracing metadata, `policy_tier` is a `PolicyTier` enum (`default`, `interactive`, `high_priority`) for queue ordering, and `ingestion_tier` is an `IngestionTier` enum (`full` for Tier 1, `metadata` for Tier 2)
- **AND** `control` carries no `pinned_target` or other target-butler directive

#### Scenario: A stale target directive is rejected
- **WHEN** an envelope arrives whose `control` still carries `pinned_target`
- **THEN** it fails IngestControlV1 validation at the ingest boundary
- **AND** no `message_inbox` or `public.ingestion_events` row is created and no goal is admitted

#### Scenario: Tier-dependent payload validation
- **WHEN** `control.ingestion_tier` is `"full"` (Tier 1)
- **THEN** `payload.raw` must be a non-None dict containing the complete provider payload
- **WHEN** `control.ingestion_tier` is `"metadata"` (Tier 2)
- **THEN** `payload.raw` must be None and `payload.normalized_text` contains only the subject line or summary

#### Scenario: Dashboard channel exemption from discretion
- **WHEN** a message is ingested with `source.channel = "dashboard"`
- **THEN** the message SHALL bypass discretion evaluation entirely (operator messages are always intentional)
- **AND** the message proceeds directly to goal admission with no classification or routing step

### Requirement: Request Context Construction
The Switchboard SHALL build an immutable request context from each accepted ingest envelope. This context SHALL travel with the envelope into goal admission and form part of the admitted goal's input; no classification or routing step SHALL consume or alter it. The `request_id` SHALL be a UUID7 identifier.

Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Request context fields
- **WHEN** a message is accepted for processing
- **THEN** the Switchboard assigns: `request_id` (UUID7, equals `public.ingestion_events.id`), `received_at` (server timestamp), `source_channel`, `source_endpoint_identity`, `source_sender_identity`, `external_conversation_id` (the resolved conversation key), `reply_target_ref` (the resolved reply target), `source_thread_identity` (the notify-facing name for the same reply target, never the conversation key), `idempotency_key`, `trace_context`, `ingestion_tier`, `dedupe_key`, `dedupe_strategy` (`"connector_api"`)
- **AND** if a global ingestion rule was evaluated: `triage_decision`, `triage_rule_id`, `triage_rule_type`, with no `triage_target` because no rule selects a butler
- **AND** if the envelope carries group-chat metadata: `participant_count`, `chat_type`, and `interaction_eligible` (only when `false`)
- **AND** if the envelope is a batch covering several senders: `source_sender_identities` (a JSON array built from `sender.participants`) and, when the connector reports one, `owner_sender_identity` (from `sender.owner_sender_id`)
- **AND** both batch keys are omitted for single-sender envelopes, whose `source_sender_identity` already names the sender

#### Scenario: Admitted goal carries the ingest reference
- **WHEN** goal admission admits a goal from an accepted envelope
- **THEN** the goal records the envelope's `request_id` as its ingest reference and receives the request context as goal input
- **AND** a duplicate submission of the same envelope returns the existing goal rather than admitting another
- **AND** no butler session receives the `request_id` as `session.request_id` or `session.ingestion_event_id`, because no domain butler spawns a session

#### Scenario: Batch envelopes preserve per-sender identity
- **WHEN** a connector flushes a buffered batch spanning several senders
- **THEN** `sender.identity` remains the collapsed sentinel `"multiple"`
- **AND** the per-sender identities MUST still reach `request_context` via `source_sender_identities`, because downstream consumers (notably passive interaction sync) resolve contacts from them and cannot resolve the sentinel

### Requirement: Statistics Pipeline (OTel/Prometheus)
Connector statistics SHALL be exported via the OTel/Prometheus metrics pipeline. The pre-aggregated SQL rollup tables (`connector_stats_hourly`, `connector_stats_daily`, `connector_fanout_daily`) were dropped by migration sw_025 (butlers-ufzc). Per-target-butler fanout metrics SHALL NOT be emitted, because no envelope is routed to a target butler.

Source: RFC 0038 D1; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Volume metrics
- **WHEN** connectors emit OTel metrics
- **THEN** per-connector volume metrics (messages_ingested, messages_failed, source_api_calls, dedupe_accepted) are available in Prometheus for dashboard time-series queries

#### Scenario: Fanout metrics
- **WHEN** an accepted connector envelope is handed to goal admission
- **THEN** no per-connector per-target-butler fanout metric is emitted, because no envelope is routed to a target butler

### Requirement: Bearer material never persists

Connectors and the Switchboard ingest boundary SHALL withhold bearer material (one-time codes,
password-reset links, magic links, and Telegram login codes) from every persisted payload. Only the
fact that an auth artifact arrived SHALL be kept, as a typed placeholder recording the artifact kind,
the provider domain, and the observation time. Scrubbing SHALL NOT change the message's
ingestion-policy decision or its goal admission.

FilteredEventBuffer.record SHALL also apply the shared detector to subject_or_preview before appending it for persistence, deriving provider domain and service-sender aggressive behavior from its existing source metadata. Detector failure SHALL withhold that preview without changing the drop decision or retaining its original text.

The preview seam SHALL treat provider and sender-domain labels as untrusted hints. It SHALL validate every emitted placeholder label, including historical unsafe placeholders, and use a fixed connector fallback or unknown rather than reflect URLs, paths, code-bearing numeric labels or a detected link secret. Genuine safe provider domains SHALL remain visible. This preview-only label policy SHALL NOT rewrite the shared detector's other callers or alter source metadata and replay payloads.

Source: RFC 0038 D1; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: One-time code is replaced by a placeholder
- **WHEN** an `ingest.v1` envelope whose text contains a one-time code (for example "Your code is 482913") reaches the ingest boundary
- **THEN** `message_inbox.raw_payload` and `message_inbox.normalized_text` SHALL NOT contain the code
- **AND** they SHALL contain a placeholder of the form `[auth-code withheld: <provider-domain>]`
- **AND** `raw_payload.control.bearer_scrubbed` SHALL be `true` and `raw_payload.bearer_artifacts` SHALL list `{kind, provider_domain, observed_at}` records
- **AND** scrubbing SHALL NOT change the message's ingestion-policy decision or its goal admission

#### Scenario: Ordinary numbers survive
- **WHEN** a message contains order numbers, dates, or amounts that are not adjacent to code wording and not part of a reset or magic link
- **THEN** the text SHALL be persisted unchanged

#### Scenario: Scrubbing is idempotent
- **WHEN** already-scrubbed text is scrubbed again
- **THEN** no further change SHALL occur

#### Scenario: Detector failure fails closed
- **WHEN** the scrubber raises while processing an envelope
- **THEN** the ingest boundary SHALL persist the message as metadata-only with `payload.raw` null and SHALL NOT persist the unscrubbed text

#### Scenario: Connectors scrub before submit
- **WHEN** a connector builds an `ingest.v1` envelope
- **THEN** it SHOULD withhold bearer material from `payload.raw` and `payload.normalized_text` and set `control.bearer_scrubbed` to `true` when it did so
- **AND** the Switchboard SHALL scrub again at ingest, so a connector that omits scrubbing does not cause persistence

#### Scenario: Dropped Gmail subject is scrubbed at the shared record seam
- **WHEN** a blocked Gmail subject contains an auth code and is recorded for the filtered-event buffer
- **THEN** the actual persisted subject_or_preview SHALL contain the typed provider-domain placeholder and SHALL NOT contain the code
- **AND** the filtered row's metadata, status, reason and existing payload tier SHALL remain intact

#### Scenario: Telegram service sender preview receives aggressive scrubbing
- **WHEN** a preview from actual numeric-string sender 777000 contains a 4–8-digit login code without auth wording
- **THEN** the shared record seam SHALL apply the detector's service-sender aggressive mode before persistence
- **AND** the typed placeholder SHALL persist and the original code SHALL be absent

#### Scenario: Central preview scrubbing preserves benign text and idempotence
- **WHEN** an ordinary non-service preview contains benign numbers/dates, is null, or is already scrubbed
- **THEN** the current detector's benign-number/date and placeholder rules SHALL remain in force and null SHALL remain null
- **AND** a planted benign row SHALL still persist, so an empty query cannot satisfy the privacy assertion

#### Scenario: Central preview detector failure withholds only the preview
- **WHEN** source hint extraction or the detector raises while preparing subject_or_preview
- **THEN** the record seam SHALL withhold the preview and SHALL NOT enqueue, persist or log its original text
- **AND** the metadata row and unchanged filter decision SHALL remain available

#### Scenario: Untrusted labels cannot reintroduce withheld bearer material
- **WHEN** current or legacy source/provider or sender hints contain a code-bearing path or DNS label, a URL with a bearer token, or a detected link secret, or an old placeholder already contains such an unsafe label
- **THEN** the current record seam and historical preview command SHALL persist a typed placeholder with a fixed safe fallback label and SHALL NOT retain the code or token in that preview
- **AND** genuine safe domains, nonstring-hint fallback, benign text, metadata and payloads SHALL retain their defined behavior
- **AND** detector failure SHALL withhold the preview without logging the raw hint, preview or error message

#### Scenario: One-shot historical preview scrub is bounded and honestly reported
- **WHEN** the separately released one-shot maintenance command processes a fixed historical cutoff
- **THEN** it SHALL reuse the shared preview detector, update only subject_or_preview in locked keyset batches, preserve other columns and report content-free complete/incomplete counts
- **AND** a complete rerun SHALL change zero rows; rollback/restart/unknown acknowledgements SHALL not skip a row or fabricate completion
- **AND** source-only implementation SHALL NOT imply live execution or reversal of already redacted bearer material

## REMOVED Requirements

### Requirement: Triage Integration
**Reason**: RFC 0038 removes pinned-target routing, thread affinity, `route_to` dispatch and LLM classification with no successor (D1, D5; RFC 0003 disposition). The requirement's remaining deterministic gating is restated without routing in `REQ-connector-base-spec-003`.
**Migration**: Connector-scoped `block` rules remain under "Source Filter Gate (Base Contract)"; global deterministic gating moves to `REQ-connector-base-spec-003`; every accepted envelope that no rule withholds is admitted as a goal (`REQ-goal-orchestration-008`) with a deterministic origin and ceiling (`REQ-capability-authorization-003`). The thread-affinity settings and overrides API has no successor.

## ADDED Requirements

### Requirement: Deterministic Ingestion Gating Before Goal Admission
Connector-scoped ingestion rules (`block` action) SHALL be evaluated at the connector, and global ingestion rules SHALL be evaluated post-ingest by the Switchboard in priority order, first match wins. No ingestion rule, envelope field, thread history or model classification SHALL select a target butler. An accepted message envelope that no global rule withholds SHALL be handed to goal admission with no classification or routing step before it; a device or telemetry feed envelope SHALL follow the feed path of `REQ-butler-switchboard-007` instead. Each connector SHALL declare at registration whether its channel is a message channel or a device or telemetry feed, and Switchboard SHALL treat an undeclared channel as a message channel, whose goals take origin from owner-channel authorization.

ID: REQ-connector-base-spec-003
Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Unmatched envelope goes to goal admission
- **WHEN** an accepted envelope matches no global ingestion rule
- **THEN** the Switchboard hands it to goal admission as goal input
- **AND** no LLM classification, thread-affinity lookup or route dispatch runs first

#### Scenario: Deterministic rule evaluation
- **WHEN** a message passes connector-scoped evaluation and is accepted by the Switchboard
- **THEN** global ingestion rules are evaluated in priority order and the first match determines the action
- **AND** no rule action selects a target butler

#### Scenario: Global skip withholds admission
- **WHEN** a global ingestion rule resolves an accepted envelope to `skip`
- **THEN** the envelope remains in `public.ingestion_events` for direct querying
- **AND** no goal is admitted for it

#### Scenario: Email thread history selects nothing
- **WHEN** an email arrives in a thread whose earlier messages were handled by goals that used one domain's capabilities
- **THEN** no thread-affinity lookup runs and the envelope reaches goal admission like any other accepted envelope

### Requirement: Connectors mark untrusted spans in owner-channel messages
`IngestPayloadV1` SHALL carry an `untrusted_spans` field: a tuple of span records, each naming a `kind` (`forwarded`, `quoted` or `embedded`) and the character range of `normalized_text` it covers. A connector SHALL mark every part of a message that its provider identifies as forwarded from someone else, quoted from an earlier message, or embedded third-party content (an attached or inlined message, a shared post). When the connector knows a message contains such content but cannot locate its boundaries, it SHALL mark the whole of `normalized_text` as one span of that kind. Attachments that carry a message from someone else SHALL be marked the same way. Admission SHALL treat an owner-origin envelope with any marked span as exposed (`REQ-capability-authorization-003`) and SHALL fence each span as data in the goal input.

ID: REQ-connector-base-spec-004
Source: RFC 0038 D5 and D6
Scope: v1-mandatory

#### Scenario: The owner forwards a Telegram message
- **WHEN** the owner forwards a message from another Telegram user to the bot
- **THEN** the Telegram connector marks the forwarded text as a `forwarded` span
- **AND** the owner goal admitted from it starts exposed, with the forwarded text fenced as data

#### Scenario: The owner replies with the earlier email quoted
- **WHEN** an owner-channel email quotes an earlier message from a third party below the owner's own text
- **THEN** the connector marks the quoted portion as a `quoted` span and leaves the owner's own text unmarked
- **AND** the goal starts exposed

#### Scenario: A forwarded message whose boundaries cannot be found
- **WHEN** a provider flags a message as forwarded but the connector cannot locate where the forwarded content starts
- **THEN** the connector marks the whole of `normalized_text` as one `forwarded` span

#### Scenario: Text pasted without quoting markers
- **WHEN** the owner pastes third-party text into an owner-channel message without any provider forwarding or quoting marker
- **THEN** no span is marked and the text is owner input, the known limit RFC 0038 D5 records
