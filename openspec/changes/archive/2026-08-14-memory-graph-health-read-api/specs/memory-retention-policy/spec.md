## ADDED Requirements

### Requirement: Graph-health coverage reuses the consolidation-aware cleanup population

The graph-health read observation SHALL use the same reapable-expired episode
predicate as `memory_episode_cleanup`: expired and non-pending, or pending
beyond the configured grace window. It SHALL use `expires_at IS NOT NULL` as
its denominator and SHALL not invent a second expiry definition, invoke the
cleanup handler, or convert observation into retention authority.

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
