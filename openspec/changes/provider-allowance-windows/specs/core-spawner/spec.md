## ADDED Requirements

### Requirement: Allowance State Write On Usage Limit
When a failover-eligible attempt fails with the `usage_limit` class, the spawner SHALL
write the attempt with outcome `allowance_exhausted` (not `runtime_failure`) and upsert
the provider account's allowance state to `exhausted` with the parsed reset
(`reset_source='parsed'`) or, when none was stated, `now()` plus the default window
(`reset_source='default_window'`). The upsert SHALL keep the latest-known reset, SHALL NOT
shorten a parsed reset with a default-window guess, and SHALL be best-effort: a write
failure SHALL NOT raise out of failover handling. A successful attempt SHALL clear an
exhausted state on its account only when it observed that same rejection before invocation,
the rejection is strictly older than its database-clock start, and the catalog entry still
names the captured account. Missing capture, an equal timestamp, or a subsequent rejection
SHALL leave the state unchanged. Every failover or cold retry SHALL capture its own context.

#### Scenario: An in-flight success cannot clear a later rejection
- **WHEN** an attempt begins, a later rejection exhausts its account, and the attempt succeeds
- **THEN** the later rejection SHALL remain exhausted with its reset and provenance unchanged
- **AND** a success begun strictly after the unchanged rejection SHALL clear it best-effort

#### Scenario: Usage limit fails over to a different account in one attempt
- **WHEN** attempt 1 fails with a usage-limit rejection
- **THEN** attempt 1 SHALL be recorded as `allowance_exhausted`, the account SHALL be marked
  exhausted, and attempt 2 SHALL run on a candidate from a different account
- **AND** no `runtime_failure` row SHALL be counted for attempt 1

#### Scenario: Ordinary failures leave allowance untouched
- **WHEN** an attempt fails with a non-usage-limit eligible failure
- **THEN** the attempt SHALL be recorded as `runtime_failure` and no allowance state SHALL
  be written
