## ADDED Requirements

### Requirement: Pending-approvals visibility follows pending-actions availability

The dashboard's Pending approvals KPI SHALL use its numeric value and
`/approvals` door only when the approval metrics query succeeds and
`meta.pending_actions_sources_degraded` is absent or empty. A query failure or
pending-actions degradation SHALL render the unavailable value with no
interactive door, name the unavailable sources, and offer a safe metrics-read
retry. `approval_rules` degradation alone SHALL not make a complete pending
approvals KPI unavailable.

The Sidebar's existing `/approvals` navigation link SHALL render a visible,
accessible amber unavailable marker instead of a numeric zero when the metrics
query fails or `meta.pending_actions_sources_degraded` is non-empty. A
truthful complete zero remains quiet.

#### Scenario: Pending-actions source is partial

- **WHEN** the metrics response names one or more
  `pending_actions_sources_degraded`
- **THEN** the Pending approvals KPI renders `—` with unavailable semantics
- **AND** it is not a link, button, or other interactive door
- **AND** the page names the unavailable source(s) and offers a retry of the
  metrics query.

#### Scenario: Rule source alone is partial

- **WHEN** the metrics response names only
  `approval_rules_sources_degraded` and contains a complete pending count
- **THEN** the Pending approvals KPI renders that count, including a genuine
  zero
- **AND** its `/approvals` door remains available.

#### Scenario: Sidebar pending badge is unavailable

- **WHEN** the metrics query fails or names one or more
  `pending_actions_sources_degraded`
- **THEN** the Sidebar keeps its existing `/approvals` link
- **AND** renders an accessible amber unavailable marker instead of `0`.
