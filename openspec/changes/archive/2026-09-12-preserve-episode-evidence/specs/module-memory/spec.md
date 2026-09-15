## ADDED Requirements

### Requirement: Content-Free Source Episode Tombstones

The memory module SHALL preserve durable provenance when an episode is deleted
without retaining that episode's raw content. Every local episode deletion
MUST atomically create or retain a tombstone containing only the episode
identifier and deletion timestamp. Facts and rules that name the deleted
episode as `source_episode_id` MUST retain that identifier; generic
`memory_links` whose source or target is that episode MUST remain resolvable as
an expired source relation. Tombstones MUST NOT contain episode content, title,
prompt, runtime output, credentials, or raw owner-message data.

#### Scenario: Expired source preserves derived fact and rule attribution

- **WHEN** a local episode with derived facts and rules is deleted
- **THEN** the deletion transaction MUST retain each durable row's
  `source_episode_id` and write a content-free tombstone for that identifier
- **AND** the derived fact and rule content MUST remain readable without a
  deleted episode body being retained

#### Scenario: Generic episode relation is not a dangling link

- **WHEN** a `memory_links` row names an episode as either source or target and
  that episode is deleted
- **THEN** the link reader MUST resolve that episode endpoint as `expired`
- **AND** it MUST NOT represent the endpoint as a live episode or silently
  omit the relation

#### Scenario: Tombstone write failure prevents silent provenance loss

- **WHEN** the tombstone write for an episode deletion cannot complete
- **THEN** the episode deletion MUST fail atomically
- **AND** no dependent fact, rule, or link MAY be left with silently erased
  provenance
