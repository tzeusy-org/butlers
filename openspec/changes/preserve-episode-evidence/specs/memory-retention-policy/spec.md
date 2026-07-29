## ADDED Requirements

### Requirement: Episode Cleanup Retains Truthful Durable Evidence

The bounded `memory_episode_cleanup` sweep SHALL rely on the memory module's
content-free source-tombstone invariant before deleting a reapable episode. It
MUST NOT null a durable fact or rule's source identifier, retain raw episode
content, or perform a historical catch-up drain as part of this requirement.

#### Scenario: Normal bounded cleanup leaves source-expired evidence

- **WHEN** the existing cleanup sweep deletes one reapable episode in its
  normal bounded batch
- **THEN** durable facts, rules, and generic links associated with that episode
  MUST remain attributable through content-free expired-source evidence
- **AND** the sweep MUST NOT retain the deleted episode's raw content

#### Scenario: This change does not authorize historical retention cleanup

- **WHEN** the source-tombstone invariant is deployed
- **THEN** it MUST NOT select, delete, backfill, or otherwise mutate any
  pre-existing retained episode solely to establish historical provenance
- **AND** a historical drain MUST remain a separately owner-authorized
  operation
