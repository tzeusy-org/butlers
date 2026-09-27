## ADDED Requirements

### Requirement: Owner notification for unknown-sender transitory entities

The system SHALL make one owner-facing notification attempt through the
standard owner-notification delivery boundary when Switchboard identity
resolution first surfaces an unknown sender as a transitory entity. This
behavior is part of the entity lifecycle: it SHALL use the transitory entity
and its reviewable `metadata.unidentified` state, and SHALL NOT require or
describe a `public.contacts`, `public.contact_info`, or `contact_id` record.

The notification SHALL identify the sender only with the safe display label
and source channel needed for review, SHALL direct the owner to the existing
Unidentified Entities review flow, and SHALL NOT include the inbound message
body or grant the unknown sender any role or approval authority.

#### Scenario: New unknown sender is surfaced for entity review

- **WHEN** reverse lookup misses an inbound Telegram sender with display name
  `Chloe L`
- **AND** the unknown-sender flow creates transitory entity `E` with
  `metadata.unidentified = true`, `source_channel = 'telegram'`, and the
  observed source value
- **THEN** the system MUST make one owner-notification attempt identifying
  `Chloe L` and the Telegram source
- **AND** the notification MUST direct the owner to the Unidentified Entities
  review flow for `E`
- **AND** the notification MUST NOT expose the inbound message body or a
  contact-table identifier

#### Scenario: Repeated sender does not create a notification storm

- **WHEN** a later message arrives from the same source channel and sender
  identifier after its transitory entity has been surfaced
- **THEN** the owner MUST NOT receive another unknown-sender notification for
  that sender
- **AND** the later message MUST continue through normal known-entity
  resolution and routing

#### Scenario: Owner-notification delivery failure does not block routing

- **WHEN** the owner-notification delivery attempt for a newly surfaced
  transitory entity fails
- **THEN** the inbound message MUST remain eligible for normal routing with its
  unknown-sender identity context
- **AND** later messages from that sender MUST NOT retry the notification on
  every ingress event

#### Scenario: Transitory entity creation fails open

- **WHEN** reverse lookup misses but the system cannot create a transitory
  entity
- **THEN** the inbound message MUST remain eligible for normal routing as an
  unresolved sender
- **AND** the system MUST NOT claim to the owner that an entity is available
  for review
