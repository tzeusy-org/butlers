## ADDED Requirements

### Requirement: Read-only memory-pool graph-health coverage

The system SHALL expose a read-only graph-health coverage observation for the
memory pools selected by the existing dashboard memory fan-out. The observation
SHALL report whether evidence is complete, incomplete, or unknown without
creating a write, job, repair, retention action, provenance mutation, or
relationship entity-fact operation.

Each completed relevant pool SHALL expose its exact cleanup-lag numerator and
denominator. Each genuinely unavailable relevant pool SHALL remain explicitly
unknown with nullable metrics. A candidate pool that genuinely lacks a memory
schema SHALL be absent rather than failed, matching the existing fan-out
contract.

#### Scenario: All relevant memory pools provide complete coverage

- **WHEN** at least one relevant memory pool completes its graph-health read
  and no relevant pool fails
- **THEN** the fleet coverage state SHALL be `complete`
- **AND** every returned pool observation SHALL have `coverage="complete"`
- **AND** no metric value SHALL be fabricated from an absent source

#### Scenario: A failed relevant pool leaves coverage incomplete

- **WHEN** one or more relevant memory pools complete their graph-health read
  and one or more relevant pools fail it
- **THEN** the fleet coverage state SHALL be `incomplete`
- **AND** every failed pool SHALL be represented as an `unknown` pool
  observation with null numerator, denominator, and ratio
- **AND** completed observations SHALL remain available only as lower-bound
  evidence, not a fleet-wide all-clear

#### Scenario: No relevant pool yields a completed observation

- **WHEN** no relevant memory pool produces a completed graph-health
  observation
- **THEN** the fleet coverage state SHALL be `unknown`
- **AND** the system SHALL not substitute zero values or a healthy verdict

### Requirement: Graph-health cleanup-lag population is exact

For every completed graph-health pool, the numerator SHALL count only retained
episodes that the ordinary cleanup sweep is currently allowed to reap. The
denominator SHALL count every episode with `expires_at IS NOT NULL`. The ratio
SHALL be numerator divided by denominator when the denominator is non-zero and
`null` when the denominator is zero.

#### Scenario: Pending episode inside the cleanup grace window is not degraded

- **WHEN** an episode is expired, has `consolidation_status='pending'`, and is
  still inside the configured cleanup grace window
- **THEN** it SHALL not contribute to the graph-health cleanup-lag numerator
- **AND** it SHALL remain part of the expiration-eligible denominator

#### Scenario: Reapable expired episode contributes to graph-health lag

- **WHEN** an episode is expired and either is non-pending or has exceeded the
  pending cleanup grace window
- **THEN** it SHALL contribute to the graph-health cleanup-lag numerator
- **AND** the observation SHALL not invoke cleanup or alter the episode

#### Scenario: Zero denominator has no fabricated percentage

- **WHEN** a completed relevant pool has no episodes with `expires_at IS NOT NULL`
- **THEN** its cleanup-lag numerator and denominator SHALL both be zero
- **AND** its cleanup-lag ratio SHALL be `null`
