## ADDED Requirements

### Requirement: Memory Overture renders expired-retention state and coverage honestly

The `/memory` `MemoryOverture` SHALL render the expired-retention observation
from `GET /api/memory/stats` using `meta.retention_status`,
`meta.retention_sources`, and `meta.retention_pools_failed`. It SHALL treat
retention state and source coverage as explicit facts, not infer health from a
missing value or a partial numeric aggregate. For a completed retention source,
it SHALL consume the `RetentionSourceObservation` wire fields
`source_butler`, `source_schema`, `expired_retained_episodes`,
`retention_eligible_episodes`, and `expired_retained_ratio` rather than an
untyped or differently named source shape.

When the status is `degraded`, the Overture SHALL name at least the affected
`source_butler` and its `expired_retained_episodes` count; it MAY also render
`retention_eligible_episodes` and `expired_retained_ratio`. When the status is
`unknown`, it SHALL name the failed sources and state that fleet retention
coverage is incomplete. It SHALL not render a healthy or all-clear retention
statement while status is `unknown`. A healthy rendering is permitted only for
a complete `healthy` response. Existing ordinary `pools_failed` and
catalog-drift degraded notes remain independently visible.

The Overture SHALL not add a run-now, re-enable, delete, drain, or owner-
authorization affordance as part of this observation-only capability.

#### Scenario: Complete healthy observation renders without an alarm claim

- **WHEN** `GET /api/memory/stats` reports `meta.retention_status='healthy'`
- **THEN** the Overture MAY render a calm retention state
- **AND** it SHALL not present a degraded or incomplete-coverage warning for
  the retention observation

#### Scenario: Degraded source is named in the Overture

- **WHEN** `GET /api/memory/stats` reports `meta.retention_status='degraded'`
  with `meta.retention_sources` containing
  `{"source_butler":"relationship","source_schema":"relationship",
  "expired_retained_episodes":2,"retention_eligible_episodes":5,
  "expired_retained_ratio":0.4}`
- **THEN** the Overture SHALL render a named retention-degraded note for that
  `source_butler`
- **AND** the note SHALL include its `expired_retained_episodes` count
- **AND** the page SHALL not offer cleanup or schedule mutation controls

#### Scenario: Incomplete coverage never renders as an all-clear

- **WHEN** `GET /api/memory/stats` reports `meta.retention_status='unknown'`
  and non-empty `meta.retention_pools_failed`
- **THEN** the Overture SHALL render an incomplete-retention-coverage note
  naming the failed sources
- **AND** it SHALL not render a healthy retention claim, zero aggregate, or
  complete ratio in place of the unknown result

#### Scenario: Retention note coexists with existing degraded-source notes

- **WHEN** the stats response contains both retention coverage failure and
  ordinary `pools_failed` or `catalog_pools_failed` metadata
- **THEN** the Overture SHALL retain the existing named degraded-source notes
- **AND** it SHALL render the retention note separately so one failure class
  cannot hide another
