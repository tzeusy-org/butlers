# Dashboard Relationship — Channel Preference Control

## MODIFIED Requirements

### Requirement: Contact channel preference is entity-keyed
The contact-detail channel control (`ContactChannelCard`) SHALL set and clear the
preferred channel through the entity `prefers-channel` fact, not through a CRM
column on `public.contacts`. The control offers the channels the contact actually
has a contact fact for. The previous `PATCH /contacts/{id}` `preferred_channel`
write path is removed.

#### Scenario: Owner sets a preferred channel
- **WHEN** the owner selects a preferred channel for a contact in the dashboard
- **THEN** an active `prefers-channel` fact is asserted for that contact's entity
- **AND** re-opening the contact shows that channel as preferred

#### Scenario: Owner clears the preferred channel
- **WHEN** the owner clears the preferred channel for a contact
- **THEN** the contact's active `prefers-channel` fact is retracted
- **AND** the control shows no channel preferred

#### Scenario: Only reachable channels are offered
- **WHEN** the channel-preference control renders for a contact
- **THEN** only channels for which the contact has a corresponding contact fact
  are selectable
