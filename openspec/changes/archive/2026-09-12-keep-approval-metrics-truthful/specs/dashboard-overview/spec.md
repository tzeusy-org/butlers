## ADDED Requirements

### Requirement: Overview does not derive approval all-clear signals from partial metrics

The dashboard overview model SHALL derive aggregate pending-approval attention
and Now signals only from a complete pending-actions metric family. When
`meta.pending_actions_sources_degraded` is non-empty, it SHALL surface a named
approval-source-unavailable signal instead of a zero or empty-derived
all-clear. Independently successful individual approval rows remain usable.

#### Scenario: Partial aggregate with no individual rows

- **WHEN** approval metrics return `total_pending = 0` with non-empty
  `meta.pending_actions_sources_degraded` and no individual pending rows are
  available
- **THEN** the overview does not render a `0 pending approvals` result or
  conclude that nothing needs attention from that aggregate
- **AND** it renders a named unavailable approval source signal.

#### Scenario: Healthy individual rows remain actionable

- **WHEN** the pending-actions metrics aggregate is partial but the individual
  pending-approvals query returns healthy rows
- **THEN** the overview continues to render those individual rows and their
  existing action affordances
- **AND** it keeps the named aggregate-unavailable signal visible.

#### Scenario: A complete zero remains calm

- **WHEN** the approval metrics response has an empty or absent
  `pending_actions_sources_degraded` list and `total_pending = 0`
- **THEN** the overview renders no approval-unavailable signal
- **AND** it preserves its normal calm zero behavior.
