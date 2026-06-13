## MODIFIED Requirements

### Requirement: Model Resolution
The system SHALL provide model resolution functions that select catalog entries at spawn
time by querying the catalog with butler-specific overrides applied.

#### Scenario: Next eligible same-tier candidate
- **WHEN** the spawner requests the next eligible model after an attempted
  `catalog_entry_id` fails or is skipped
- **THEN** the resolver SHALL search only the exact effective complexity tier that
  produced the original candidate
- **AND** it SHALL apply global catalog values plus butler override COALESCE semantics
- **AND** it SHALL exclude all previously attempted or skipped `catalog_entry_id` values
- **AND** it SHALL return the next highest-priority enabled model in that same tier

#### Scenario: Initial tier fallthrough remains separate
- **WHEN** initial model resolution finds no candidate in the requested tier
- **THEN** the existing canonical tier fallthrough behavior MAY select a candidate from
  the next eligible tier
- **AND** any subsequent failover attempts SHALL remain restricted to the effective tier
  that produced that selected candidate

#### Scenario: State filter applies to failover candidates
- **WHEN** a next-candidate query evaluates model catalog rows
- **THEN** models in error, offline, deprecated, rate-limited, anomaly, or disabled states
  SHALL NOT be returned as failover candidates

#### Scenario: Deterministic fallback ordering
- **WHEN** multiple non-attempted candidates remain in the effective tier
- **THEN** fallback ordering SHALL be deterministic by effective priority descending,
  then `created_at ASC`, then `id ASC`
- **AND** the resolver SHALL NOT return an already-attempted candidate
