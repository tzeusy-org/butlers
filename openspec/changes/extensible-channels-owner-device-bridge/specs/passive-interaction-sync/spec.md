## ADDED Requirements

### Requirement: Source-Aware Interaction Identity and Discord Scoring
Message-based interaction sync SHALL identify a daily interaction by the stable tuple
`(entity_id, source_channel, interaction_date, direction, source_endpoint_identity)` and SHALL use
an attested endpoint identity persisted by Switchboard. After source-aware enforcement is enabled,
this requirement SHALL supersede the earlier hour-offset mechanism: `valid_at` records a real event
time, `interaction_date` is the event's UTC calendar date, and no source consumes a numbered hour
slot. Scoring eligibility remains an explicit
relationship-sync contract and MUST NOT be inferred from inbound catalog registration alone.

ID: REQ-passive-interaction-sync-001
Source: RFC 0033 §Source-aware interaction identity (Proposed amendment to RFC 0013 D4)
Scope: v1-mandatory

#### Scenario: Discord bot-token message resolves to a relationship
- **WHEN** interaction sync processes an eligible shipped Discord bot-token message whose sender identity is a Discord user ID
- **THEN** it SHALL resolve that sender through the canonical `has-handle` value `discord:<user_id>`
- **AND** a resolved non-owner sender SHALL contribute a Discord interaction fact under the same direction and group-size scoring rules as other eligible message sources

#### Scenario: Source endpoint is server-attested
- **WHEN** interaction sync derives the stable key for a message group
- **THEN** `source_endpoint_identity` SHALL come from Switchboard-persisted request context associated with the accepted envelope
- **AND** caller metadata, fact metadata, display names, or an inbound source catalog row MUST NOT override that endpoint identity

#### Scenario: Replay and concurrent sync create one fact
- **WHEN** repeated or concurrent job runs process the same entity, source channel, calendar date, direction, and attested endpoint
- **THEN** exactly one active interaction fact SHALL exist for that tuple
- **AND** every loser SHALL observe the existing fact rather than inserting a second fact or reporting an error

#### Scenario: Distinct source dimensions do not collide
- **WHEN** the same entity has interactions on the same date and direction from two source channels or two attested endpoints
- **THEN** each distinct stable tuple SHALL retain its own active fact
- **AND** adding a thirteenth or later explicitly scoring-enabled channel SHALL require no new timestamp offset or finite channel slot

#### Scenario: Real event time replaces channel marker time
- **WHEN** a source group contains one or more eligible messages for an interaction date
- **THEN** the fact's `valid_at` SHALL be a deterministic real timestamp from that group rather than a channel-assigned hour
- **AND** changing the represented timestamp SHALL not be used to distinguish source channel, direction, or endpoint

#### Scenario: Staged cutover preserves legacy runs
- **WHEN** the source-aware persistence key has not yet been deployed and verified by every interaction writer
- **THEN** the existing hour-offset behavior SHALL remain in force for those writers
- **AND** source-aware enforcement SHALL not activate until compatibility checks prove the stable-key writer, constraints, and readers are present

#### Scenario: Discord scoring does not broaden Discord OAuth
- **WHEN** Discord joins passive interaction sync
- **THEN** only events from the already-shipped bot-token connector contract SHALL be eligible
- **AND** no OAuth user-context token, scope, consent, guild access, or direct-message authority SHALL be added or inferred
