## ADDED Requirements

### Requirement: Inbound message identity resolution

The Switchboard SHALL call `resolve_contact_by_channel(type, value)` on every inbound message before routing. The resolution MUST use the message's source channel type (e.g., `'telegram'`, `'email'`) and source identifier (e.g., Telegram chat ID, email address) to look up the sender in `shared.contact_info`.

#### Scenario: Owner sends a Telegram message

- **WHEN** a Telegram message arrives from chat ID `99999`
- **AND** `resolve_contact_by_channel('telegram', '99999')` returns a contact with `roles = ['owner']`
- **THEN** the Switchboard MUST identify the sender as the owner

#### Scenario: Known non-owner sends a Telegram message

- **WHEN** a Telegram message arrives from chat ID `12345`
- **AND** `resolve_contact_by_channel('telegram', '12345')` returns a contact "Chloe" with `roles = []` and `entity_id = 'abc-123'`
- **THEN** the Switchboard MUST identify the sender as "Chloe" with entity_id `abc-123`

#### Scenario: Unknown sender sends a Telegram message

- **WHEN** a Telegram message arrives from chat ID `55555`
- **AND** `resolve_contact_by_channel('telegram', '55555')` returns `None`
- **THEN** the Switchboard MUST trigger the temporary contact creation flow (per `contacts-identity` spec)

#### Scenario: Email message identity resolution

- **WHEN** an email arrives from `chloe@example.com`
- **AND** `resolve_contact_by_channel('email', 'chloe@example.com')` returns a contact
- **THEN** the Switchboard MUST identify the sender using the resolved contact

---

### Requirement: Identity-enriched prompt injection

After resolving the sender's identity, the Switchboard MUST inject a structured identity preamble into the prompt before routing to downstream butlers. The preamble format depends on the sender's identity resolution result.

#### Scenario: Owner message prompt injection

- **WHEN** the sender is resolved as the owner contact
- **THEN** the routed prompt MUST be prefixed with `[Source: Owner, via {channel}]`
- **AND** the original message text MUST follow the preamble

#### Scenario: Known non-owner message prompt injection

- **WHEN** the sender is resolved as a known contact "Chloe" with `contact_id = 'abc-123'` and `entity_id = 'def-456'`
- **THEN** the routed prompt MUST be prefixed with `[Source: Chloe (contact_id: abc-123, entity_id: def-456), via telegram]`
- **AND** downstream butlers MUST use `entity_id` as the subject when storing facts from this message

#### Scenario: Unknown sender prompt injection

- **WHEN** the sender is an unknown sender with a newly created temporary contact `temp_id = 'ghi-789'` and `entity_id = 'jkl-012'`
- **THEN** the routed prompt MUST be prefixed with `[Source: Unknown sender (contact_id: ghi-789, entity_id: jkl-012), via telegram -- pending disambiguation]`

#### Scenario: Downstream butler attributes fact to correct entity

- **WHEN** the Switchboard routes `[Source: Chloe (contact_id: abc-123, entity_id: def-456), via telegram] I had lunch at 2pm today` to the Relationship butler
- **THEN** the Relationship butler MUST store the fact "had lunch at 2pm" with `entity_id = 'def-456'` (Chloe's entity), NOT the owner's entity

---

### Requirement: Routing log identity enrichment

The Switchboard's `routing_log` table SHALL be extended to store resolved identity alongside the raw `source_id`. The following columns SHALL be added: `contact_id UUID`, `entity_id UUID`, and `sender_roles TEXT[]`.

#### Scenario: Routing log captures resolved identity

- **WHEN** a message from a known contact is routed
- **THEN** the `routing_log` entry MUST include the resolved `contact_id`, `entity_id`, and `sender_roles` alongside the existing `source_channel` and `source_id`

#### Scenario: Routing log captures unknown sender

- **WHEN** a message from an unknown sender is routed
- **THEN** the `routing_log` entry MUST include the temporary `contact_id` and `entity_id`
- **AND** `sender_roles` MUST be `'{}'` (empty array)

---

### Requirement: Owner vs non-owner message differentiation

The Switchboard MUST differentiate message handling based on whether the sender has the `'owner'` role. Owner messages are treated as first-person instructions. Non-owner messages are treated as third-party communications that the butler system processes on behalf of the owner.

#### Scenario: Owner message treated as first-person instruction

- **WHEN** the owner sends "Remind me to call Mom at 5pm"
- **THEN** the Switchboard MUST route this as a first-person instruction from the owner
- **AND** the downstream butler MUST create a reminder for the owner

#### Scenario: Non-owner message treated as third-party communication

- **WHEN** Chloe (non-owner) sends "I had lunch at 2pm today"
- **THEN** the Switchboard MUST route this with Chloe's identity context
- **AND** the downstream butler MUST store the fact against Chloe's entity, NOT the owner's

#### Scenario: Non-owner message that instructs the system

- **WHEN** Chloe (non-owner) sends "Remind me to call the dentist"
- **THEN** the Switchboard MUST route this with Chloe's identity context
- **AND** the downstream butler MUST create a reminder attributed to Chloe, NOT the owner
- **AND** the reminder notification MUST be sent to Chloe's channel (subject to approval gating since Chloe is non-owner)
