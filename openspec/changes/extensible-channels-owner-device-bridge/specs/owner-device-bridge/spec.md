## Purpose

Reserves a trustworthy owner-device calls/SMS bridge while keeping it inactive until the owner
approves an exact provider, authentication, privacy, retention, and ingress contract.

## ADDED Requirements

### Requirement: Unapproved Owner-Device Bridge Stays Inactive
The owner-device bridge SHALL remain inactive until one exact reviewed artifact records the owner-
selected provider, provisioned account and number identity, authentication mechanism, webhook
ingress route, enabled event kinds, privacy profile, retention period, revocation behavior, and
regional/provider retry and acknowledgment prerequisites. This draft MUST NOT reserve provider-specific catalog slugs,
create credentials, expose ingress, activate a connector, or make SMS deliverable.

ID: REQ-owner-device-bridge-001
Source: RFC 0033 §Owner decision and activation gate (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: No exact owner act exists
- **WHEN** the owner has not approved one exact provider/auth/privacy artifact
- **THEN** the bridge SHALL report `not_approved` and no Connect, Activate, Test, or Send effect SHALL be available
- **AND** no provider credential, phone number, webhook route, source pair, or outbound adapter SHALL be created or enabled

#### Scenario: Partial setup cannot activate
- **WHEN** only some prerequisites are present, including a credential without an approved privacy profile or a provider account without authenticated ingress
- **THEN** activation SHALL fail closed with the missing prerequisite categories
- **AND** the response MUST NOT reveal whether any credential or phone number value exists

#### Scenario: Provider eligibility remains an external prerequisite
- **WHEN** the selected provider cannot confirm account eligibility, number capability, regional availability, or applicable registration requirements
- **THEN** the bridge SHALL remain inactive
- **AND** the system SHALL not substitute a provider, number, region, or compliance assertion

### Requirement: [TARGET-STATE] Owner-Device Inbound Authentication and Identity
An approved owner-device connector SHALL authenticate every webhook before durable acceptance,
bind events to the configured provider account and receiving number, and use provider-native stable
event identifiers. Provider credentials and identity-bound verification material SHALL be stored as
Tier 2 secured owner `entity_info`; no secret SHALL enter logs, metrics, browser payloads, catalog
rows, ingest metadata, or generic audit detail.

ID: REQ-owner-device-bridge-002
Source: RFC 0033 §Owner-device authentication and identity (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: Signed event matches configured account and number
- **WHEN** a webhook has a valid provider signature and freshness proof and names the configured account and receiving number
- **THEN** the connector SHALL derive the owner-device endpoint identity from server-held configuration
- **AND** it SHALL map the provider event identifier to stable ingestion idempotency without trusting a caller-supplied account or endpoint identity

#### Scenario: Forged, stale, or misdirected event is rejected
- **WHEN** signature validation fails, freshness is outside the approved window, or the event targets another account or number
- **THEN** the connector SHALL reject it before persistence or Switchboard submission
- **AND** the audit result SHALL contain only a bounded failure category

#### Scenario: Twilio remains ineligible under the generic webhook gate
- **WHEN** an implementation proposes Twilio using only its signed incoming-message URL/parameters/body and stable MessageSid
- **THEN** the bridge SHALL remain inactive because those facts do not provide this requirement's signed freshness proof or configured non-2xx retry contract
- **AND** no MessageSid lookup, provider read, webhook URL mutation/rotation, or fallback SHALL be inferred
- **AND** Twilio eligibility SHALL require a separate exact owner-approved provider contract

#### Scenario: Sender resolves through phone identity
- **WHEN** an authenticated inbound SMS or call event contains a remote E.164 party
- **THEN** identity resolution SHALL use the existing `has-phone` relationship identity contract
- **AND** an unresolved party SHALL follow the existing unknown-sender behavior without creating a provider-specific identity shortcut

### Requirement: [TARGET-STATE] Bounded Calls and SMS Event Mapping
The first approved bridge SHALL limit ingestion to the owner-selected subset of inbound SMS and
call-lifecycle events. Call perception SHALL exclude audio, recording, transcription, live media,
and call-control effects; adding any of those requires a new approved contract.

ID: REQ-owner-device-bridge-003
Source: RFC 0033 §Owner-device event mapping (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: Inbound SMS becomes one canonical event
- **WHEN** the provider reports a newly received SMS for the configured number
- **THEN** the connector SHALL emit at most one canonical event for the provider message identifier
- **AND** the body and raw provider fields SHALL follow the exact approved privacy profile rather than a connector default

#### Scenario: Call lifecycle updates remain ordered facts
- **WHEN** the provider emits initiation, ringing, answered, and terminal updates for one call
- **THEN** each authenticated provider event SHALL retain its own stable event identifier and the shared provider call identity
- **AND** duplicate or out-of-order delivery SHALL not create duplicate lifecycle facts or regress a later state to an earlier state

#### Scenario: Audio-bearing behavior is excluded
- **WHEN** a call event includes a recording URL, transcript, media stream, or audio attachment
- **THEN** the bridge SHALL discard that material before canonical ingestion
- **AND** it SHALL not fetch, record, transcribe, store, or expose the audio-bearing resource

### Requirement: [TARGET-STATE] Exact Privacy Profile and Retention
Before activation, the owner SHALL choose and approve either metadata-only SMS or content-enabled
SMS with an exact finite source-content retention period and explicit acceptance of the derived and
backup survival boundaries below. Call lifecycle ingestion SHALL be metadata-only. Status,
telemetry, audit, and setup surfaces SHALL remain content-blind under every profile.

ID: REQ-owner-device-bridge-004
Source: RFC 0033 §Privacy profiles and retention (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: Metadata-only SMS profile
- **WHEN** the owner selects metadata-only SMS perception
- **THEN** after authenticated in-memory verification, canonical events SHALL use `ingestion_tier="metadata"`, `payload.raw=null`, and a body-free fixed summary
- **AND** no SMS body SHALL enter connector persistence, Switchboard raw or normalized content, routing/classification, session artifacts, memory, facts, episodes, embeddings, audit, telemetry, or product reads

#### Scenario: Content-enabled SMS profile
- **WHEN** the owner selects content-enabled SMS perception with an exact retention period
- **THEN** the connector SHALL admit the SMS body only through the standard protected ingest payload path
- **AND** the body SHALL be absent from logs, metrics, catalog/status APIs, generic audit metadata, and browser setup payloads

#### Scenario: Source-content expiry redacts direct persisted copies
- **WHEN** content-enabled SMS reaches the approved source-content retention deadline
- **THEN** a lineage-aware sweep SHALL replace the SMS body and provider payload fields with a fixed redaction marker in `connectors.filtered_events.full_payload` and `subject_or_preview`, `switchboard.dead_letter_queue.original_payload`, `switchboard.message_inbox.raw_payload` and `normalized_text`, each routed butler's `route_inbox.route_envelope`, linked `{schema}.sessions.prompt`, and linked `{schema}.session_process_logs.command` or `stderr` when they contain the verbatim source content
- **AND** it SHALL preserve body-free identifiers, timestamps, lifecycle state, deduplication keys, routing outcomes, and redaction evidence rather than deleting canonical lineage rows

#### Scenario: Canonical ingestion registry remains body-free
- **WHEN** either SMS privacy profile creates a `public.ingestion_events` row
- **THEN** that row SHALL contain source, event, deduplication, tier, and routing metadata only and SHALL contain no SMS body or raw provider payload
- **AND** the metadata row, including body-free source/sender identity such as an E.164 party, MAY survive source-content expiry as canonical lineage and SHALL be disclosed as such before activation

#### Scenario: Derived semantic data follows separate retention
- **WHEN** content-enabled SMS has produced `sessions.result`, `sessions.tool_calls`, facts, memories, episodes, summaries, or embeddings
- **THEN** source-content expiry SHALL not claim to identify, retract, or erase those derived artifacts
- **AND** they MAY survive under their owning retention policies unless a separately approved lineage-cascade contract governs them
- **AND** the setup surface SHALL disclose this survival before content-enabled activation

#### Scenario: Backup and export boundary is explicit
- **WHEN** source content expires in the live database
- **THEN** exports created after expiry SHALL contain only the redacted live representation, while owner-controlled exports made earlier are not retroactively modified
- **AND** managed backups MAY retain pre-expiry source content until their own configured expiry, and a restored backup SHALL run the owner-device retention sweep before normal runtime or product reads resume

#### Scenario: Owner requires complete downstream erasure
- **WHEN** the owner does not accept survival of derived artifacts or managed-backup copies until their independent expiry
- **THEN** content-enabled SMS SHALL remain unavailable
- **AND** activation SHALL require a separately approved cross-system lineage-cascade and backup-erasure contract; the metadata-only profile remains the bounded option

### Requirement: [TARGET-STATE] Content-Blind Setup, Status, and Revocation
The owner-device setup surface SHALL explain the selected provider, requested capabilities, privacy
profile, retention, external ingress exposure, and outbound SMS status before activation. It SHALL
be keyboard operable, expose visible focus, acknowledge pending operations, and distinguish
`not_approved`, `not_configured`, `connecting`, `active`, `degraded`, and `revoked` without exposing
secret or communication content.

ID: REQ-owner-device-bridge-005
Source: RFC 0033 §Owner setup and revocation experience (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: First glance explains authority and privacy
- **WHEN** the owner opens the bridge setup surface
- **THEN** it SHALL show the selected provider, inbound event kinds, privacy profile, direct-copy retention period, body-free canonical identity survival, derived/export/backup survival, ingress exposure, and whether outbound SMS is unsupported
- **AND** no credential value, full phone number, sender or recipient, message body, or provider payload SHALL be present

#### Scenario: Activation is repeat-safe and truthful
- **WHEN** the owner invokes an approved activation action more than once while it is pending
- **THEN** at most one activation attempt SHALL proceed and the control SHALL expose its pending state immediately
- **AND** failure SHALL preserve the previous inactive state and name a safe recovery action

#### Scenario: Revocation stops effects and preserves safe evidence
- **WHEN** the owner revokes the bridge
- **THEN** new provider reads and authenticated webhook acceptance SHALL stop, stored connector credentials SHALL become unavailable to the runtime, and status SHALL become `revoked`
- **AND** a content-blind audit event SHALL record the owner-derived actor, action, time, provider category, and outcome

### Requirement: SMS Delivery Remains a Separate Future Effect
This bridge SHALL NOT deliver outbound SMS. A later owner-approved change MUST define and implement
the Messenger adapter, recipient and reachability rules, per-message approval interception,
provider handoff evidence, idempotency/reconciliation capabilities, partial-effect reporting, and
rollback before any SMS send can be activated.

ID: REQ-owner-device-bridge-006
Source: RFC 0033 §Outbound SMS exclusion and future activation gate (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Inbound bridge cannot send
- **WHEN** an owner-device connector is eventually configured for inbound calls or SMS
- **THEN** no inbound credential, source catalog row, webhook, checkpoint, or connector health state SHALL authorize an outbound provider call
- **AND** `notify(channel="sms")` SHALL retain the unsupported-channel result

#### Scenario: Ambiguous future handoff cannot be retried blindly
- **WHEN** a future approved SMS adapter loses its result after provider handoff may have begun
- **THEN** the delivery SHALL be classified as ambiguous unless provider evidence proves the same immutable attempt can be reconciled or repeated safely
- **AND** neither a generic retry nor a new idempotency key SHALL issue an unproven duplicate send
