## MODIFIED Requirements

### Requirement: Needs Attention List

The home page SHALL render a `Needs attention` list composed from current state from
`GET /api/issues`, the canonical butler liveness verdict, pending approvals, bounded
notification delivery pressure, and active QA staffer state. A row SHALL represent either
live state or a time-bounded recent failure; older issue and notification records remain
available as history and SHALL NOT make the list or briefing imply that the system is
currently unhealthy. The list is a rule-separated attention surface, not a card grid or
table.

#### Scenario: Unknown latest QA patrol status surfaces as attention

- **WHEN** `GET /api/qa/summary` reports `staffer_status = "unknown_patrol_status"`
  for a latest completed patrol and its circuit breaker is not tripped
- **THEN** the attention list renders a high-severity `QA patrol status unknown` row
  linking to `/qa`
- **AND** the row explains that the latest patrol reported an unrecognized status
  without rendering the raw stored value as UI copy
- **AND** the same condition appears in the Overview's `Now` list and SHALL NOT be
  omitted as healthy, calm, or no QA attention
- **AND** a tripped breaker continues to take precedence over this row
