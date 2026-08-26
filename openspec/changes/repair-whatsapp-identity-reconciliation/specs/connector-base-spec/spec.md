## ADDED Requirements

### Requirement: LLM-facing sender identity normalization

A batching connector MUST keep transport identifiers as structured machine data and MUST normalize
device-qualified or provider-opaque sender identities before constructing any text or conversation
history that an LLM can consume.

ID: REQ-connector-base-spec-001
Source: heart-and-soul/architecture.md connector normalization boundary
Scope: v1-mandatory

#### Scenario: Mapped WhatsApp LID is normalized everywhere LLM-visible

- **WHEN** the WhatsApp connector receives a sender LID with a phone mapping
- **THEN** participant metadata and conversation history MUST carry the normalized phone JID identity
- **AND** normalized text and the speaker display label MUST NOT contain the raw LID

#### Scenario: Device ordinal is not a person identity

- **WHEN** a WhatsApp sender JID contains a device ordinal
- **THEN** every structured sender identity leaving connector normalization MUST omit the ordinal
- **AND** all messages from that person's devices MUST use the same normalized identity

#### Scenario: Unmapped opaque identity remains structured and hidden

- **WHEN** a WhatsApp LID has no available phone mapping
- **THEN** the connector MUST retain a stable structured identity for deterministic unknown-sender
  reservation
- **AND** every LLM-visible speaker label MUST use a neutral label rather than the LID

#### Scenario: Provider payload remains available for audit

- **WHEN** sender identity is normalized for LLM-facing history
- **THEN** the full-tier provider payload MAY retain its original transport fields for internal audit
- **AND** those raw fields MUST NOT be promoted into normalized text or speaker names
