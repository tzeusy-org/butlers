## MODIFIED Requirements

### Requirement: Owner notification for unknown-sender transitory entities

The system SHALL make one owner-facing notification attempt through the
standard owner-notification delivery boundary when Switchboard identity
resolution first surfaces an unknown sender as a transitory entity. This
behavior is part of the entity lifecycle: it SHALL use the transitory entity
and its reviewable `metadata.unidentified` state, and SHALL NOT require or
describe a `public.contacts`, `public.contact_info`, or `contact_id` record.

Before making that delivery attempt, the system SHALL atomically persist a
durable notification claim keyed by the source channel and sender identifier.
Only the caller that obtains the claim MAY make the delivery attempt. A
delivery failure SHALL leave the claim in place, and a claim-persistence
failure SHALL continue normal routing without making an unclaimed owner
delivery attempt.

Before the unknown sender's identity context is activated in the routing
pipeline, the system SHALL atomically reserve or reuse one transitory entity
for that source channel and sender identifier. This reservation SHALL be
durable across concurrent ingress workers and SHALL NOT write
`relationship.entity_facts` from Switchboard; the existing relationship-owned
post-resolution assertion remains responsible for the channel fact.

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
- **THEN** the system MUST atomically reserve one owner-notification attempt
  for that sender
- **AND** the system MUST make that attempt identifying `Chloe L` and the
  Telegram source
- **AND** the notification MUST direct the owner to the Unidentified Entities
  review flow for `E`
- **AND** the notification MUST NOT expose the inbound message body or a
  contact-table identifier

#### Scenario: Repeated sender does not create a notification storm

- **WHEN** a later message arrives from the same source channel and sender
  identifier after its transitory entity has been surfaced
- **THEN** the atomic claim MUST prevent another unknown-sender notification
  attempt for that sender
- **AND** the later message MUST continue through normal known-entity
  resolution and routing

#### Scenario: Concurrent first messages reuse one transitory entity

- **WHEN** two first messages from the same source channel and sender
  identifier arrive concurrently with different display labels
- **AND** both reverse lookups miss before the relationship-owned channel-fact
  assertion runs
- **THEN** the system MUST mint at most one transitory entity for that sender
- **AND** both pipeline activations MUST carry that same entity UUID in their
  unknown-sender identity context
- **AND** Switchboard MUST NOT write the sender channel fact directly

#### Scenario: Owner-notification delivery failure does not block routing

- **WHEN** the owner-notification delivery attempt for a newly surfaced
  transitory entity fails
- **THEN** the inbound message MUST remain eligible for normal routing with its
  unknown-sender identity context
- **AND** the durable claim MUST remain sealed so later messages from that
  sender do not retry the notification on every ingress event

#### Scenario: Notification-claim failure fails open without an owner send

- **WHEN** the system cannot persist the atomic notification claim for a newly
  surfaced transitory entity
- **THEN** the inbound message MUST remain eligible for normal routing with its
  unknown-sender identity context
- **AND** the system MUST emit an observable failure signal
- **AND** the system MUST NOT make an owner-notification attempt without a
  durable claim

#### Scenario: Transitory entity creation fails open

- **WHEN** reverse lookup misses but the system cannot create a transitory
  entity
- **THEN** the inbound message MUST remain eligible for normal routing as an
  unresolved sender
- **AND** the system MUST NOT claim to the owner that an entity is available
  for review
