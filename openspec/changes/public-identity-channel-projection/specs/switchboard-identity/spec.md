## MODIFIED Requirements

### Requirement: Inbound message identity resolution

The Switchboard SHALL call `resolve_contact_by_channel(type, value)` on every inbound message before routing. The resolution MUST use the message's source channel type (e.g., `'telegram'`, `'email'`) and source identifier (e.g., Telegram chat ID, email address) to look up the sender in `public.identity_channel_projection` via channel-handle predicates (a `has-handle` triple whose value is prefixed `telegram:<id>`, a `has-email` triple, etc.). Roles and canonical name SHALL be read from `public.entities`.
- The Switchboard fleet startup wiring SHALL enable this resolution for its
production `MessagePipeline` and provide a non-null callback for the
entity-identity owner-notification boundary. That callback SHALL use the
standard `notify.v1` Switchboard-to-Messenger delivery path, not a direct
connector or contacts-table path.
- These are derived Relationship-owned triples, not a second canonical store. Resolution MUST preserve current eligibility, normalization and complete live-entity ambiguity and MUST NOT use private-schema direct/view/definer reads. Missing, ambiguous or unavailable projection MUST confer no owner authority and MUST retain existing unresolved-sender routing, transitory reservation and durable notification failure semantics.

ID: REQ-switchboard-identity-003
Source: RFC 0004 Amendment 4 §§4.1, 4.5; heart-and-soul/vision.md rule 3
Scope: v1-mandatory

#### Scenario: Owner sends a Telegram message

- **WHEN** a Telegram message arrives from chat ID `99999`
- **AND** `resolve_contact_by_channel('telegram', '99999')` returns an entity whose `public.entities.roles = ['owner']`
- **THEN** the Switchboard MUST identify the sender as the owner

#### Scenario: Known non-owner sends a Telegram message

- **WHEN** a Telegram message arrives from chat ID `12345`
- **AND** `resolve_contact_by_channel('telegram', '12345')` returns the entity "Chloe" with `roles = []` and `entity_id = 'abc-123'`
- **THEN** the Switchboard MUST identify the sender as "Chloe" with entity_id `abc-123`

#### Scenario: Unknown sender sends a Telegram message

- **WHEN** a Telegram message arrives from chat ID `55555`
- **AND** `resolve_contact_by_channel('telegram', '55555')` returns `None`
- **THEN** the Switchboard MUST invoke the unknown-sender transitory-entity
  flow defined by `entity-identity`
- **AND** the flow MUST NOT create or require a `public.contacts` or
  `public.contact_info` row
- **AND** the entity-only sender's `contact_id` and
  `source_sender_contact_id` MUST be null or omitted, never a newly minted
  temporary-contact identifier
- **AND** concurrent first messages for the same sender MUST reuse one
  transitory `entity_id` before their routing contexts are activated, even
  when their display labels differ
- **AND** that minting reservation MUST NOT move
  `relationship.entity_facts` writes into Switchboard
- **AND** owner-notification behavior for a successfully surfaced transitory
  entity MUST follow `entity-identity`'s owner-notification requirement

#### Scenario: Fleet activation supplies the standard owner-delivery callback

- **WHEN** the Switchboard daemon wires its production `MessagePipeline`
- **THEN** the pipeline MUST enable identity resolution
- **AND** the pipeline MUST receive a non-null owner-notification callback
- **AND** an unknown-sender notification from that callback MUST use the
  `notify.v1` Switchboard-to-Messenger delivery path

#### Scenario: Email message identity resolution

- **WHEN** an email arrives from `chloe@example.com`
- **AND** `resolve_contact_by_channel('email', 'chloe@example.com')` returns an entity
- **THEN** the Switchboard MUST identify the sender using the resolved entity

#### Scenario: Missing projected sender does not read private facts

- **WHEN** an inbound identifier has no eligible projected match, including during an incomplete backfill
- **THEN** the Switchboard MUST treat the sender as unresolved without private-schema lookup
- **AND** ordinary routing and existing transitory reservation/notification failure behavior MUST remain available
- **AND** the system MUST NOT claim established or owner identity from a private fact, name or credential fallback

#### Scenario: Projection outage does not claim identity or completed recovery

- **WHEN** the projection is unavailable to the actual Switchboard runtime role
- **THEN** the message MUST remain eligible for ordinary unresolved routing without owner authority
- **AND** bounded failure diagnostics MUST distinguish infrastructure failure from successful resolution or completed backfill without exposing handles
