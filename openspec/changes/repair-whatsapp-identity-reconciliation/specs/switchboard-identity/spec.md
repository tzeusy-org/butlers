## ADDED Requirements

### Requirement: WhatsApp transport identity normalization

The Switchboard MUST interpret the `whatsapp_user_client` transport channel as the canonical
`whatsapp_jid` identity type for sender resolution and identity-fact assertion while preserving the
original transport channel in ingestion and routing records.

ID: REQ-switchboard-identity-001
Source: heart-and-soul/architecture.md connector normalization boundary
Scope: v1-mandatory

#### Scenario: Known WhatsApp sender reuses a phone-matched entity

- **WHEN** a WhatsApp user-client message carries an individual JID whose phone digits uniquely match
  one live entity's active phone identity
- **THEN** the Switchboard MUST resolve that existing entity before routing
- **AND** it MUST NOT create a transitory entity for the JID

#### Scenario: Ambiguous WhatsApp phone remains unresolved

- **WHEN** a WhatsApp individual JID matches more than one live entity under the canonical bounded
  phone comparison
- **THEN** the Switchboard MUST treat the sender as unresolved
- **AND** it MUST NOT select the first matching entity

#### Scenario: Unknown WhatsApp sender uses the existing transitory flow

- **WHEN** a normalized WhatsApp sender identity matches no live entity
- **THEN** the Switchboard MUST reserve or reuse one transitory entity through the existing
  unknown-sender flow
- **AND** the relationship-owned identity assertion MUST record the normalized channel identity

#### Scenario: Transport channel remains unchanged outside identity resolution

- **WHEN** a `whatsapp_user_client` message is persisted, evaluated by ingestion policy, or reported
  through connector telemetry
- **THEN** its source channel MUST remain `whatsapp_user_client`
- **AND** the identity-only translation MUST NOT change replay, policy, or metric grouping

### Requirement: Buffered conversation per-speaker identity

For buffered conversations, the Switchboard MUST deterministically resolve every distinct structured
sender identity and attach the resulting entity anchor to each speaker's messages before signal
extraction and fan-out.

ID: REQ-switchboard-identity-002
Source: docs/superpowers/specs/2026-08-24-whatsapp-identity-reconciliation-design.md §3
Scope: v1-mandatory

#### Scenario: Multi-speaker batch resolves each speaker once

- **WHEN** a buffered WhatsApp conversation contains messages from multiple distinct sender identities
- **THEN** the Switchboard MUST resolve each distinct identity once for that batch
- **AND** every message from the same identity MUST reuse the same resolution result

#### Scenario: Primary routing sender reuses the batch resolution

- **WHEN** the Switchboard selects one non-owner participant as the top-level routing sender
- **THEN** the identity preamble and routing context MUST reuse that participant's batch resolution
- **AND** the participant MUST NOT be resolved or surfaced a second time

#### Scenario: Unknown group participant receives an entity anchor

- **WHEN** one participant in a buffered group conversation is genuinely unresolved
- **THEN** that participant MUST receive or reuse a transitory entity anchor before fan-out
- **AND** facts about another participant MUST NOT be attributed to that transitory entity

#### Scenario: Per-speaker identity failure is content-blind

- **WHEN** identity resolution fails for one speaker while routing remains fail-open
- **THEN** that speaker MUST retain a neutral non-identifier display label
- **AND** the routed excerpt MUST omit its entity anchor rather than inventing or borrowing one
