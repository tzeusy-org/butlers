## ADDED Requirements

### Requirement: Usage-Limit Failure Class
The classifier SHALL recognize provider plan usage-limit exhaustion as its own failure
class, `usage_limit`, held in a dedicated `_USAGE_LIMIT_MARKERS` bucket that is disjoint
from `_RATE_LIMIT_MARKERS`. The decision SHALL be failover-eligible under the same
pre-tool-call gates as other systemic failures and SHALL carry a reset instant only when
the provider message states one unambiguously (an epoch, an ISO-8601 instant with an
offset, or a relative duration) and that instant lies after now and within eight days.
After the switch the goal runner SHALL use this classification as the runtime failure
taxonomy that RFC 0038 D14 names: `usage_limit` is a retryable class, and the
classification never decides authority, grants or budget.

ID: REQ-model-failover-001
Source: openspec change provider-allowance-windows (bu-q7vx1q.13); RFC 0038 D14; generalist collision disposition re-scope (bu-q7vx1q.90.2)
Scope: v1-mandatory

#### Scenario: Usage limit is not a transient rate limit
- **WHEN** a runtime invocation with no captured tool calls fails with
  `"You've hit your usage limit"`
- **THEN** the classifier SHALL return `eligible=True` with a reason prefixed
  `usage_limit`
- **AND** a plain `"429 Too Many Requests"` SHALL still return `rate_limit_before_work`

#### Scenario: An ambiguous reset is not guessed
- **WHEN** the usage-limit message states only a clock time such as `"try again at 12:25 PM"`
- **THEN** the decision SHALL carry no reset instant

#### Scenario: Stated reset is parsed
- **WHEN** the message is `"Claude AI usage limit reached|<epoch>"` with a future epoch
- **THEN** the decision SHALL carry that instant as its reset

### Requirement: Account-Scoped Allowance Exclusion
Every candidate resolver (`_RESOLVE_SQL`, the intent-aware candidate query, and
`_NEXT_SAME_TIER_SQL`) SHALL exclude every enabled catalog entry whose provider account
key (`model_catalog.allowance_account`, else `runtime_type`) has a
`provider_allowance_states` row with `state='exhausted'` and `reset_at` in the future,
regardless of the attempted-id list. The intent-aware resolution receipt SHALL record such
candidates as `excluded_allowance`. After the switch the same exclusion SHALL apply to the
goal runner's per-invocation model selection by role and tier (RFC 0038 D16), to the model
of a D14 relaunch and to the next tier a `goal.escalate` launches, so allowance selects
which model an invocation of an admitted goal runs on. Exclusion SHALL NOT admit a new
goal, change the goal's origin, ceiling, grants or exposure, or add to or reset its budget.
When the exclusion leaves no eligible candidate, the invocation SHALL end as the D14
non-retryable path states, with no wait inside the goal.

ID: REQ-model-failover-002
Source: openspec change provider-allowance-windows (bu-q7vx1q.13); RFC 0038 D10, D14, D16; generalist collision disposition re-scope (bu-q7vx1q.90.2)
Scope: v1-mandatory

#### Scenario: Failover skips a sibling on the exhausted account
- **WHEN** two catalog entries share an account key, the account is exhausted until a
  future reset, and a third entry is on a different account
- **THEN** both entries on the exhausted account SHALL be excluded from every resolver
- **AND** the entry on the different account SHALL be selected

#### Scenario: Exclusion lifts at the reset
- **WHEN** `reset_at` passes
- **THEN** entries on that account SHALL be eligible again with no cleanup job

#### Scenario: Unknown state never excludes
- **WHEN** an account's state is `unknown`
- **THEN** routing SHALL NOT exclude its entries
- **AND** no reader SHALL present that account as available capacity

#### Scenario: A goal relaunch selects a different account inside the same goal
- **WHEN** after the switch a goal invocation fails with a trusted `usage_limit` and the
  broker recorded no capability call made with its token
- **THEN** the goal runner's one D14 relaunch SHALL resolve a same-tier model on an account
  that is not exhausted
- **AND** the relaunch SHALL keep the `goal_id`, role, brief, grants and exposure, count
  against the goal's invocation budget and admit no new goal

#### Scenario: No unexhausted candidate ends the invocation without waiting
- **WHEN** after the switch every same-tier candidate for a failed goal invocation is on an
  exhausted account
- **THEN** the goal runner SHALL NOT relaunch it or hold it until the reset
- **AND** a child ends `failed` with the decision returned to its lead, while a lead's
  failure ends the goal `failed` with its ledger intact

### Requirement: Allowance Outcome Is Not A Breaker Signal
A usage-limit rejection SHALL be recorded as attempt outcome `allowance_exhausted`. The
dispatch-outcome circuit breaker SHALL ignore it, so it neither trips nor resets a
breaker. The same SHALL hold for attempt rows the goal runner writes after the switch.

ID: REQ-model-failover-003
Source: openspec change provider-allowance-windows (bu-q7vx1q.13); RFC 0038 D14, D17; generalist collision disposition re-scope (bu-q7vx1q.90.2)
Scope: v1-mandatory

#### Scenario: Repeated usage-limit rejections do not open the breaker
- **WHEN** ten `allowance_exhausted` attempts exist for one catalog entry
- **THEN** that entry's breaker SHALL be closed
