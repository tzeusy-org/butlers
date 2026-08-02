## ADDED Requirements

### Requirement: Approval readers preserve unavailable source evidence

The `/approvals` surface SHALL distinguish a successful empty metrics,
autonomy-suggestions, or rule-promotion response from a failed or degraded
read. A family-specific approval metrics failure SHALL name the affected pool
and never imply a zero-derived all-clear. A whole suggestions or promotion
query failure SHALL render a named unavailable state with a read-only retry;
an error after cached cards or a cached tile SHALL retain that usable evidence
alongside the unavailable state.

#### Scenario: Pending metrics are partial

- **WHEN** approval metrics include non-empty
  `meta.pending_actions_sources_degraded`
- **THEN** `/approvals` names the unavailable pending-actions source(s) and
  exposes a retry of the metrics read
- **AND** it does not present the partial pending count as a complete empty or
  all-clear result
- **AND** the independently fetched queue remains visible according to its own
  source health.

#### Scenario: Rule metrics are partial without affecting action metrics

- **WHEN** approval metrics include non-empty
  `meta.approval_rules_sources_degraded` but no pending-actions degradation
- **THEN** `/approvals` names the unavailable rule source(s) and exposes a
  retry of the metrics read
- **AND** it does not classify a partial active-rule count as a real zero
- **AND** it does not label a healthy pending-actions result unavailable.

#### Scenario: A suggestions reader fails before returning data

- **WHEN** an autonomy-suggestions or rule-promotion-suggestions query fails
  before a successful response is available
- **THEN** its section renders a named unavailable state and a retry control
- **AND** it does not hide the section by treating the missing response as an
  empty suggestions list.

#### Scenario: A promotion reader retains cached evidence on refresh failure

- **WHEN** a rule-promotion suggestions or statistics query fails after cached
  cards or statistics are available
- **THEN** the cached cards or tile remain visible
- **AND** a named unavailable state and retry control are rendered beside that
  stale evidence
- **AND** an existing per-block `meta.sources_degraded` note continues to hide
  only its affected fabricated-zero block.
