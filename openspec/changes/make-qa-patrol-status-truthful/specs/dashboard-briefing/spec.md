## MODIFIED Requirements

### Requirement: Attention Item Sources

The endpoint SHALL populate `state.attention_items` from five sources before classification:
butler liveness, grouped error entries from the `dashboard_audit_log` table, pending
approvals, failed notifications, and QA state. An attention item SHALL represent either a
live state or a time-bounded recent failure; historical aggregates SHALL remain outside
`state.attention_items` and SHALL NOT affect briefing classification, headline, or
elaboration. Each source is fetched independently and concurrently; a failure in one
source MUST NOT prevent the others from contributing.

#### Scenario: Unknown recent QA patrol status is attention, not calm

- **WHEN** the QA source reads its latest non-running patrol in the current 24-hour
  horizon and that persisted `status` is outside the canonical patrol vocabulary,
  while the circuit breaker is not tripped
- **THEN** it adds one high-severity `QA patrol status unknown` attention item with
  `source = "qa"` and a link to `/qa`
- **AND** the item explains that QA reported an unrecognized patrol status without
  exposing the raw stored value as display copy
- **AND** the condition SHALL NOT be classified as quiet, a healthy all-clear, or
  ordinary no-history state
- **AND** a tripped breaker continues to take precedence over this item
