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
- **WHEN** a configured Watched Source has no enabled exact pair in the inbound catalog
- **THEN** the connector SHALL refuse activation with a bounded configuration error
- **AND** it SHALL not open a provider connection or submit an envelope

### Requirement: Watched Source Lifecycle Conformance
Every Watched Source SHALL implement first-baseline behavior, source filtering, filtered-event
flush, replay-queue drain, checkpointing, heartbeat, metrics, rate limiting, backoff, and graceful
shutdown as required by `connector-base-spec`. Webhook profiles SHALL additionally authenticate the
provider before acknowledging an event as accepted.

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
