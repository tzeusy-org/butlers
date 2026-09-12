## ADDED Requirements

### Requirement: Approval metrics identify partial source families

`GET /api/approvals/metrics` SHALL distinguish a configured source family with
zero rows from a `pending_actions` or `approval_rules` family that could not be
fully read. It SHALL retain successful numeric contributions and expose failed
source names in `meta.pending_actions_sources_degraded` and
`meta.approval_rules_sources_degraded`, respectively. When either list is
non-empty, `meta.sources_degraded` SHALL contain their de-duplicated union.
Absent or empty family-specific lists mean that family's numeric zero is a
truthful complete result.

#### Scenario: No configured pools produce genuine zeroes

- **WHEN** no registered approvals source has `pending_actions` or
  `approval_rules`
- **THEN** the endpoint returns its normal zero-valued metrics response
- **AND** neither family is marked degraded
- **AND** clients may treat `total_pending = 0` and `active_rules_count = 0` as
  complete values.

#### Scenario: A pending-actions pool fails after another succeeds

- **WHEN** one `pending_actions` pool returns metrics and another cannot be
  discovered or queried
- **THEN** the endpoint returns HTTP 200 with the healthy pool's contributions
- **AND** `meta.pending_actions_sources_degraded` names the failed pool
- **AND** `meta.sources_degraded` names that failed pool
- **AND** `total_pending` and every decision-derived metric are partial values,
  never proof that the fleet has no pending approvals.

#### Scenario: An approval-rules pool fails independently

- **WHEN** all pending-actions pools return successfully but an
  `approval_rules` pool cannot be discovered or queried
- **THEN** the pending and decision metrics remain complete and usable
- **AND** `meta.approval_rules_sources_degraded` names the failed pool
- **AND** `active_rules_count` is treated as partial rather than a trustworthy
  zero.
