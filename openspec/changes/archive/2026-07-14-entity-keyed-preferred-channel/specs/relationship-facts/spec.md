# Relationship Facts — Preferred Channel Predicate

## ADDED Requirements

### Requirement: prefers-channel predicate
The `relationship.entity_facts` store SHALL support a `prefers-channel`
predicate expressing a contact's preferred outbound channel. It is seeded into
`relationship.entity_predicate_registry` with `object_kind='literal'` and
`cardinality='single'`. The `object` is a channel name (e.g. `"telegram"`,
`"email"`, `"discord"`). It MAY name any channel, including channels `notify()`
cannot yet deliver.

#### Scenario: Assert a preferred channel
- **WHEN** a `prefers-channel` fact is asserted for an entity that has a contact
  fact for that channel (e.g. an active `has-handle` `telegram:…` for
  `object="telegram"`)
- **THEN** an active `prefers-channel` triple is stored for that entity with the
  channel name as `object`

#### Scenario: Preference is single-valued (supersession)
- **WHEN** an entity already has an active `prefers-channel` fact and a new
  `prefers-channel` fact is asserted for the same entity
- **THEN** the prior triple is marked `validity='superseded'`
- **AND** exactly one active `prefers-channel` triple remains for that entity

#### Scenario: Clearing the preference
- **WHEN** the preferred channel is cleared for an entity
- **THEN** the active `prefers-channel` triple is marked `validity='retracted'`
- **AND** the entity has no active `prefers-channel` triple

#### Scenario: Reject preference for an unreachable channel
- **WHEN** a `prefers-channel` fact is asserted for a channel the entity has no
  corresponding contact fact for (no `has-handle`/`has-email`/`has-phone` of that
  channel family)
- **THEN** the assertion is rejected with an error naming the missing contact fact
