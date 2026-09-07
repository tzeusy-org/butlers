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

#### Scenario: Existing scope and participant gates still apply
- **WHEN** a Discord event falls outside the configured guild/channel allowlist or exceeds the existing interaction participant gate
- **THEN** it SHALL remain excluded from passive interaction evidence according to the shipped connector and connector-base contracts
- **AND** relationship scoring SHALL not turn an excluded event into an eligible one

#### Scenario: OAuth v2 remains unresolved
- **WHEN** Discord relationship scoring is enabled under this change
- **THEN** authentication SHALL remain the shipped bot-token Gateway model
- **AND** the system SHALL request no new Discord OAuth scope, user token, consent, guild visibility, or direct-message access
