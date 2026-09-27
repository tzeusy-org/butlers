## MODIFIED Requirements

### Requirement: Graph-health coverage reuses the consolidation-aware cleanup population

The graph-health read observation SHALL use the same reapable-expired episode
predicate as `memory_episode_cleanup`: expired and non-pending, or pending
beyond the configured grace window. It SHALL use `expires_at IS NOT NULL` as
its denominator and SHALL not invent a second expiry definition, invoke the
cleanup handler, or convert observation into retention authority.

ID: REQ-memory-retention-policy-009

#### Scenario: Observation remains aligned with cleanup without performing cleanup

- **WHEN** graph-health coverage is read for a memory pool
- **THEN** its reapable-expired numerator SHALL match the population the cleanup
  sweep may delete at that instant
- **AND** the read SHALL not invoke the cleanup handler, delete an episode,
  re-enable a schedule, or trigger a retention job

#### Scenario: Grace-protected pending episode is observed but not counted as lag

- **WHEN** a pending episode is expired but remains inside the cleanup grace
  window
- **THEN** it SHALL be included in the expiration-eligible denominator
- **AND** it SHALL be excluded from the reapable-expired numerator

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

#### Scenario: Source-tombstone invariant never drains pre-existing episodes

- **WHEN** the source-tombstone invariant is in effect
- **THEN** it MUST NOT select, delete, backfill, or otherwise mutate any
  pre-existing retained episode solely to establish historical provenance
- **AND** a historical drain MUST remain a separately owner-authorized
  operation
