## ADDED Requirements

### Requirement: Whole-population stalled approval radar

The flat `GET /api/approvals` endpoint SHALL accept `state=stalled` in
addition to its existing states. A stalled approval SHALL be derived only when
its persisted `status` is exactly `approved` and its `execution_result` is
`NULL`; stalled SHALL NOT be persisted as a new status or inferred from a time
threshold.

Every flat approvals response, regardless of requested state, offset, or
limit, SHALL include `meta.stalled_count`: the count of all currently stalled
actions across the endpoint's eligible approval-source population. The list
filter and the aggregate SHALL use the same per-pool eligibility and exact
stalled predicate.

#### Scenario: Stalled filter selects only approved actions without execution

- **WHEN** `GET /api/approvals?state=stalled` reads a population containing
  approved actions with null and non-null execution results plus other statuses
- **THEN** it returns only actions whose status is `approved` and whose
  `execution_result` is null
- **AND** it does not return an `executed`, `pending`, `rejected`, `expired`,
  or approved action with a non-null execution result

#### Scenario: Stalled metadata is independent of the page window

- **WHEN** `GET /api/approvals?state=decided&limit=30` returns a bounded
  history page while more than 30 older/newer rows exist
- **THEN** `meta.stalled_count` equals the count of every eligible stalled
  approval, not the count of rows on that page
- **AND** the same count is returned for `state=waiting`, `state=decided`,
  `state=all`, and `state=stalled` requests over the same healthy population

#### Scenario: Degraded approval sources cannot imply an all-clear

- **WHEN** any eligible approval source cannot supply its list or stalled
  aggregate contribution
- **THEN** the flat response identifies that source in `meta.sources_degraded`
- **AND** any returned `meta.stalled_count` is treated as observed partial
  coverage rather than proof that no stalled approvals exist

### Requirement: Trust Console verdict uses stalled radar metadata

The approvals Trust Console verdict opener SHALL derive its stalled-approval
clause from the flat response's `meta.stalled_count`, never by inspecting the
bounded decided-history rows. When the response names degraded sources, the
opener SHALL visibly name incomplete source coverage and SHALL NOT present zero
as a calm all-clear.

#### Scenario: History-window eviction cannot hide a stalled approval

- **WHEN** the decided-history response contains no stalled row because it is
  limited to its recent history window but its metadata has
  `stalled_count > 0`
- **THEN** the verdict opener reports the stalled count
- **AND** it does not conclude that no approval is stalled from the empty or
  bounded history rows

#### Scenario: Degraded stalled radar remains explicit

- **WHEN** the flat response has `meta.sources_degraded` and a zero or partial
  `meta.stalled_count`
- **THEN** the verdict opener names the unavailable source coverage
- **AND** it does not render a healthy no-stalled-approvals conclusion
