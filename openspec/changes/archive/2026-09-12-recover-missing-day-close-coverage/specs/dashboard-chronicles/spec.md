# Dashboard Chronicles — Spec Delta for recover-missing-day-close-coverage

## ADDED Requirements

### Requirement: Missing Day-Close Recovery

The Chronicles archive SHALL offer explicit regeneration for a selected
settled local day only when it has either an admissible stale cache entry or a
typed, successfully read missing-witness coverage gap. Regeneration SHALL keep
the selected date/timezone tuple and truthful pre-action state authoritative
until the same tuple is successfully re-fetched.

#### Scenario: Proven missing-witness gap is recoverable

- **WHEN** a selected settled briefing resolves `state_class=unavailable`
- **AND** its availability ledger reports both `coverage_floor` and
  `coverage_witness` as `available`
- **AND** no named briefing subquery is `unavailable`
- **THEN** the page SHALL render an accessible Regenerate action for that
  selected `(date, timezone)` tuple
- **AND** activating it SHALL POST the exact selected tuple to the existing
  day-close refresh endpoint
- **AND** the unavailable state SHALL remain visible while the request is
  pending or if it fails
- **AND** a successful response SHALL re-fetch that same briefing tuple

#### Scenario: Unproven or failed coverage is not offered regeneration

- **WHEN** the selected briefing has no availability ledger, either coverage
  read is not `available`, any named subquery is `unavailable`, or its state is
  `no_data`, `degraded`, unknown, or content-bearing without stale cache
- **THEN** the page SHALL NOT offer day-close regeneration
- **AND** it SHALL preserve the existing truthful state-specific presentation

#### Scenario: Regeneration control communicates progress and failure

- **WHEN** regeneration is pending for the selected tuple
- **THEN** its control SHALL be disabled and expose busy state accessibly
- **WHEN** regeneration fails
- **THEN** the control SHALL become available again
- **AND** the page SHALL announce an actionable failure without substituting
  cached or generated prose
