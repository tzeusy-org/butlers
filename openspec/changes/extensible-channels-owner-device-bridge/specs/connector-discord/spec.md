## ADDED Requirements

### Requirement: Shipped Discord Events as Passive Interaction Evidence
The shipped Discord bot-token Gateway connector SHALL provide stable, source-authentic interaction
evidence for passive relationship sync using the existing `discord/discord` ingest pair. This
requirement MUST NOT enable or modify the target-state OAuth user-context model.

ID: REQ-connector-discord-001
Source: RFC 0033 §Discord boundary (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Stable message and sender identities are preserved
- **WHEN** the shipped connector emits an eligible Discord message event
- **THEN** `event.external_event_id` SHALL be the provider message ID, `sender.identity` SHALL be the provider user ID, and `source.endpoint_identity` SHALL be the connector's startup-resolved bot endpoint
- **AND** display names, guild names, and channel names MUST NOT replace any of those stable identities

#### Scenario: Duplicate Gateway observation stays idempotent
- **WHEN** the same Discord message is observed again after reconnect, resume, replay, or concurrent delivery
- **THEN** the connector SHALL reuse the same provider message ID and its existing source-scoped idempotency identity
- **AND** Switchboard acceptance and downstream interaction sync SHALL not create a duplicate canonical event or interaction fact

#### Scenario: Authenticated direct-message context is eligible
- **WHEN** the shipped bot-token connector can prove from its authenticated Gateway/REST context that a message belongs to Discord channel type `DM`, with no `guild_id`
- **THEN** it SHALL persist `chat_type="private"`, `participant_count=2`, and `interaction_eligible=true` in the canonical request context
- **AND** passive interaction sync SHALL apply DM group weight to the one non-owner sender

#### Scenario: Guild, group, or unknown context is ineligible
- **WHEN** a Discord message has a `guild_id`, is a group-DM or other non-DM channel type, or lacks enough authenticated context to prove channel type `DM`
- **THEN** the connector SHALL persist `interaction_eligible=false` and SHALL not label the context as private
- **AND** passive interaction sync SHALL create no interaction fact from that event

#### Scenario: Existing source allowlist still applies
- **WHEN** a Discord event falls outside the configured guild/channel allowlist
- **THEN** it SHALL remain excluded according to the shipped connector contract
- **AND** relationship scoring SHALL not turn an excluded event into an eligible one

#### Scenario: OAuth v2 remains unresolved
- **WHEN** Discord relationship scoring is enabled under this change
- **THEN** authentication SHALL remain the shipped bot-token Gateway model
- **AND** the system SHALL request no new Discord OAuth scope, user token, consent, guild visibility, or direct-message access
