## Purpose

Defines a reusable watched-source connector profile for external event feeds while preserving the
standard connector, Switchboard, privacy, and lifecycle boundaries.

## ADDED Requirements

### Requirement: Watched Source Is a Connector Profile
A Watched Source SHALL be a standalone transport adapter that converts one explicitly configured
external source into canonical `ingest.v1` events. It MUST satisfy the connector-base contract and
MUST NOT classify, route, invoke a domain butler directly, or treat catalog registration as runtime
activation.

ID: REQ-connector-watched-source-001
Source: RFC 0033 §Watched Source profile (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: Configured source enters through Switchboard
- **WHEN** an active Watched Source observes a provider event that passes its source filter
- **THEN** it SHALL normalize and submit the event through the existing Switchboard `ingest.v1` boundary
- **AND** all deduplication, policy, classification, and routing decisions SHALL remain owned by Switchboard

#### Scenario: Catalog row alone does not start a connector
- **WHEN** a source channel/provider pair exists and is enabled in the inbound catalog
- **THEN** no provider session, webhook listener, polling loop, or background task SHALL start unless a separately configured Watched Source instance is enabled

#### Scenario: Missing catalog pair blocks activation
- **WHEN** Switchboard's source-pair preflight denies or cannot authoritatively check a configured Watched Source pair
- **THEN** the connector SHALL refuse activation with a bounded denied or unavailable error
- **AND** it SHALL not open a provider connection or submit an envelope

### Requirement: Switchboard Source-Pair Preflight
Before opening a provider connection, a Watched Source SHALL call the Switchboard MCP tool
`source.pair.preflight` at the exact configured Switchboard MCP origin with a
`source_pair_preflight.v1` request naming its connector type, canonical channel/provider, and trusted
configured endpoint identity. Switchboard SHALL authenticate the existing connector-base bearer
token first, derive the connector principal, allowed source-pair scope, allowed endpoint identities,
and Switchboard audience from server-held token authority, and compare every request field with that
principal. Caller-supplied identity is never authority. Only then SHALL Switchboard read the catalog
and return a bounded `authorized`, `denied`, or `unavailable` decision with an opaque authorization
reference, catalog generation, `checked_at`, and `valid_until`. The connector MUST NOT receive
direct catalog access.

ID: REQ-connector-watched-source-005
Source: RFC 0033 §Watched Source activation preflight (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: Authorized preflight permits a bounded connection lease
- **WHEN** an active bearer principal matches the connector type, exact source pair, configured connector endpoint identity, and configured Switchboard audience and Switchboard finds that pair in a fresh catalog snapshot
- **THEN** it SHALL return `status="authorized"` with `valid_until` no later than that snapshot's 60-second expiry
- **AND** the connector MAY open its configured provider connection only while that authorization remains current and only at the exact configured Switchboard MCP origin
- **AND** the response SHALL carry no token, token digest, principal claims, credential, or endpoint identity

#### Scenario: Missing or invalid principal is rejected before catalog lookup
- **WHEN** the bearer token is missing, invalid, expired, revoked, bound to another connector type, source pair, connector endpoint identity, or Switchboard audience, or the client follows a redirect or calls another origin
- **THEN** Switchboard SHALL reject the preflight before any catalog read and SHALL return no authorization reference
- **AND** the connector SHALL not authenticate to, poll, subscribe to, or acknowledge an event from the provider

#### Scenario: Authenticated denial or unavailability prevents provider effects
- **WHEN** a valid matching principal receives `denied` or `unavailable`, the MCP call fails, or the response expires before the connection opens
- **THEN** the connector SHALL remain inactive and SHALL not authenticate to, poll, subscribe to, or acknowledge an event from the provider
- **AND** the response and connector status SHALL expose only a bounded error code

#### Scenario: Cross-connector credential cannot borrow source authority
- **WHEN** a valid connector A bearer token requests connector B's source pair or configured endpoint identity
- **THEN** Switchboard SHALL reject it before catalog lookup even when the requested catalog pair is enabled
- **AND** no request field, heartbeat row, or connector registry row SHALL widen the bearer principal's source scope

#### Scenario: Active connector renews before authorization expiry
- **WHEN** an active connector approaches `valid_until`
- **THEN** it SHALL obtain a new `authorized` preflight before observing another provider event
- **AND** failure to renew SHALL stop the provider session and event acquisition at expiry

#### Scenario: Disable race remains fail-closed at ingest
- **WHEN** a pair is disabled after preflight authorization but before the connector submits an event
- **THEN** Switchboard's authoritative per-envelope catalog validation SHALL reject the pair on refresh or snapshot expiry
- **AND** preflight SHALL provide no reservation, write privilege, or bypass of the per-envelope decision

### Requirement: Watched Source Lifecycle Conformance
Every Watched Source SHALL implement first-baseline behavior, source filtering, filtered-event
flush, replay-queue drain, checkpointing, heartbeat, metrics, rate limiting, backoff, and graceful
shutdown as required by `connector-base-spec`. A webhook profile SHALL authenticate and normalize a
bounded request, obtain durable Switchboard acceptance, and only then return a provider-success 2xx.

ID: REQ-connector-watched-source-002
Source: RFC 0033 §Watched Source lifecycle (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: First observation establishes a baseline
- **WHEN** a delta-detecting Watched Source starts without a checkpoint
- **THEN** it SHALL establish a bounded first baseline without emitting historical events as newly observed activity
- **AND** it SHALL persist a checkpoint only after the baseline is complete

#### Scenario: Restart resumes from durable checkpoint
- **WHEN** an instance restarts after its last accepted event
- **THEN** it SHALL resume from the durable provider cursor or event identity
- **AND** a repeated provider event SHALL reuse the same external event and idempotency identities so duplicate acceptance remains success

#### Scenario: Webhook authentication fails closed
- **WHEN** a webhook signature, timestamp, destination, or configured provider account cannot be authenticated
- **THEN** the connector SHALL reject the request before filtering, persistence, acknowledgment as accepted, or Switchboard submission
- **AND** its logs and metrics SHALL identify only a bounded failure category, never the credential, body, sender, or recipient

#### Scenario: Raw request is buffered only for verification
- **WHEN** a webhook request is at most 1,048,576 bytes and requires its exact URL, form parameters, or raw JSON bytes for provider signature validation
- **THEN** the connector MAY hold that request in process memory only for bounded parsing and signature verification
- **AND** the verification buffer SHALL not be persisted, logged, traced, metered by content, sent to an LLM, or treated as an accepted canonical event
- **AND** a request exceeding the bound SHALL be rejected before full buffering

#### Scenario: Successful acknowledgment follows durable acceptance
- **WHEN** an authenticated webhook event is normalized and Switchboard returns `accepted` or `duplicate` for its stable provider event identity
- **THEN** the connector SHALL return the provider-success 2xx response
- **AND** it SHALL release the verification-only raw buffer after the request completes

#### Scenario: Unknown durability returns a retryable provider failure
- **WHEN** Switchboard rejects the event, is unavailable, or its acceptance result is lost or unknown
- **THEN** the connector SHALL return a non-2xx response and SHALL not represent the event as accepted
- **AND** any provider retry SHALL reuse the same external event and idempotency identities so an earlier accepted attempt deduplicates

### Requirement: Per-Source Failure Isolation and Partial Effects
One Watched Source instance SHALL isolate provider accounts or configured endpoints from each other.
An event checkpoint MUST NOT advance past an event whose durable acceptance is unknown, and a
successful source MUST NOT be rolled back because another source fails.

ID: REQ-connector-watched-source-003
Source: RFC 0033 §Watched Source failure and checkpoint boundary (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: One source failure does not stall another
- **WHEN** one configured source enters provider backoff while another source has valid events
- **THEN** the healthy source SHALL continue through its independent loop and checkpoint
- **AND** health output SHALL identify the failed source by safe instance identity and bounded failure class

#### Scenario: Acceptance ambiguity does not skip an event
- **WHEN** the connector loses the Switchboard result after submission and cannot prove whether the event was accepted
- **THEN** it SHALL retain the previous checkpoint and retry with the same event and idempotency identities
- **AND** Switchboard deduplication SHALL make repeated acceptance non-duplicating

#### Scenario: Filtered event is durably accounted before checkpoint
- **WHEN** a provider event is excluded by the source filter
- **THEN** the connector SHALL satisfy the connector-base filtered-event flush obligation before advancing beyond it
- **AND** a flush failure SHALL leave the checkpoint behind that event for safe recovery

### Requirement: Watched Source Deactivation and Privacy Boundary
Deactivation or credential revocation SHALL stop new provider reads and webhook acceptance, close
the connector lifecycle cleanly, and preserve already accepted canonical history according to its
existing retention policy. Status and audit projections MUST remain content-blind.

ID: REQ-connector-watched-source-004
Source: RFC 0033 §Watched Source deactivation and privacy (Proposed; owner sign-off required)
Scope: v1-reserved

#### Scenario: Revocation stops future observation
- **WHEN** the owner revokes the Watched Source credential or disables the instance
- **THEN** the instance SHALL stop provider polling or authenticated webhook acceptance and report an inactive state
- **AND** it SHALL emit no newly observed event after the revocation boundary

#### Scenario: Deactivation preserves accepted history
- **WHEN** an instance is disabled or removed
- **THEN** existing canonical ingestion and downstream records SHALL remain governed by their existing retention and provenance contracts
- **AND** deactivation SHALL not replay, rewrite, or silently delete those records

#### Scenario: Status is content-blind
- **WHEN** the owner views Watched Source status or audit history
- **THEN** the surface MAY show source type, safe instance label, enabled state, freshness, counts, and bounded failure category
- **AND** it MUST NOT show credentials, webhook signatures, message or call bodies, sender or recipient identifiers, raw provider errors, or raw payloads
