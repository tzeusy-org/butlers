## ADDED Requirements

### Requirement: Allowance State Write On Usage Limit
When a failover-eligible attempt fails with the `usage_limit` class, the spawner SHALL
write the attempt with outcome `allowance_exhausted` (not `runtime_failure`) and upsert
the provider account's allowance state to `exhausted` with the parsed reset
(`reset_source='parsed'`) or, when none was stated, `now()` plus the default window
(`reset_source='default_window'`). The upsert SHALL keep the latest-known reset, SHALL NOT
shorten a parsed reset with a default-window guess, and SHALL be best-effort: a write
failure SHALL NOT raise out of failover handling. A successful attempt SHALL clear an
exhausted state on its account. After the switch the goal runner SHALL perform the same
write and clear for goal invocations, and the spawner SHALL keep it only for model use
outside goals that still runs through it. The allowance write SHALL NOT write owner quota
rows, goal budget rows or breaker failures, and a replayed or repeated rejection SHALL only
refresh the existing row.

ID: REQ-core-spawner-019
Source: openspec change provider-allowance-windows (bu-q7vx1q.13); RFC 0038 D10, D14, D17, D18; generalist collision disposition re-scope (bu-q7vx1q.90.2)
Scope: v1-mandatory

#### Scenario: Usage limit fails over to a different account in one attempt
- **WHEN** attempt 1 fails with a usage-limit rejection
- **THEN** attempt 1 SHALL be recorded as `allowance_exhausted`, the account SHALL be marked
  exhausted, and attempt 2 SHALL run on a candidate from a different account
- **AND** no `runtime_failure` row SHALL be counted for attempt 1

#### Scenario: Ordinary failures leave allowance untouched
- **WHEN** an attempt fails with a non-usage-limit eligible failure
- **THEN** the attempt SHALL be recorded as `runtime_failure` and no allowance state SHALL
  be written

#### Scenario: A goal invocation's usage limit marks the account without touching the goal budget
- **WHEN** after the switch a goal invocation fails with a trusted `usage_limit`
- **THEN** the goal runner SHALL record the attempt as `allowance_exhausted` with the
  invocation's `invocation_id` and `goal_id`, and mark the account exhausted
- **AND** the goal's consumed budget SHALL NOT be reset, and no `token_limits` row SHALL be
  written
