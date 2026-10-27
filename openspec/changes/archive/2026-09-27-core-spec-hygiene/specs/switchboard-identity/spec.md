## MODIFIED Requirements

### Requirement: Inbound message identity resolution

The Switchboard SHALL call `resolve_contact_by_channel(type, value)` on every inbound message before routing. The resolution MUST use the message's source channel type (e.g., `'telegram'`, `'email'`) and source identifier (e.g., Telegram chat ID, email address) to look up the sender in `relationship.entity_facts` via channel-handle predicates (a `has-handle` triple whose value is prefixed `telegram:<id>`, a `has-email` triple, etc.). Roles and canonical name SHALL be read from `public.entities`.

The Switchboard fleet startup wiring SHALL enable this resolution for its
production `MessagePipeline` and provide a non-null callback for the
entity-identity owner-notification boundary. That callback SHALL use the
standard `notify.v1` Switchboard-to-Messenger delivery path, not a direct
connector or contacts-table path.

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

### Requirement: Identity-enriched prompt injection

After resolving the sender's identity, the Switchboard MUST inject a structured identity preamble into the prompt before routing to downstream butlers. The preamble format depends on the sender's identity resolution result. The text preamble carries `entity_id` only and MUST NOT emit `contact_id`; `entity_id` is the canonical preamble identifier. An entity-only unknown sender MUST NOT gain a contact identifier merely to populate the preamble or routing context.

#### Scenario: Owner message prompt injection

- **WHEN** the sender is resolved as the owner entity with `entity_id = 'def-456'`
- **THEN** the routed prompt MUST be prefixed with `[Source: Owner (entity_id: def-456), via {channel}]`
- **AND** the original message text MUST follow the preamble
- **AND** downstream butlers MUST use `entity_id` as the anchor when storing facts about the owner

#### Scenario: Known non-owner message prompt injection

- **WHEN** the sender is resolved as a known entity "Chloe" with `entity_id = 'def-456'`
- **THEN** the routed prompt MUST be prefixed with `[Source: Chloe (entity_id: def-456), via telegram]`
- **AND** downstream butlers MUST use `entity_id` as the subject when storing facts from this message

#### Scenario: Entity-only unknown sender prompt injection

- **WHEN** the sender is unknown and the transitory-entity flow surfaces
  `entity_id = 'jkl-012'` with no `contact_id`
- **THEN** the routed prompt MUST be prefixed with `[Source: Unknown sender (entity_id: jkl-012), via telegram -- pending disambiguation]`
- **AND** the preamble MUST NOT include a contact identifier

#### Scenario: Downstream butler attributes fact to correct entity

- **WHEN** the Switchboard routes `[Source: Chloe (entity_id: def-456), via telegram] I had lunch at 2pm today` to the Relationship butler
- **THEN** the Relationship butler MUST store the fact "had lunch at 2pm" with `entity_id = 'def-456'` (Chloe's entity), NOT the owner's entity
