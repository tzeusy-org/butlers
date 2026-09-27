## ADDED Requirements

### Requirement: Runtime Schedule Toggle Is Idempotent and Authority-Bound

The scheduler SHALL expose a canonical `schedule_toggle` action in the
`scheduling` core-tool group. The action SHALL accept a schedule identifier and
an explicit requested `enabled` boolean, lock the addressed row, and persist
`enabled` and `next_run_at` atomically for `source='db'` rows. Enabling SHALL
compute the next cron occurrence using the row's stored timezone and the
daemon's deterministic stagger key; disabling SHALL clear `next_run_at`.

The action SHALL return a server-observed receipt containing the schedule ID,
name, source, requested state, observed state, whether a change landed, the
outcome (`applied` or `already_requested`), and safe `schedule.toggle` audit
evidence. Repeating the same requested state SHALL be a successful unchanged
outcome and SHALL NOT flip the row again.

TOML-owned rows and rows with any other non-DB source SHALL remain unchanged
and SHALL return typed bounded refusals. A missing row SHALL return a typed
missing refusal. These refusal results SHALL not include prompts, job
arguments, or other schedule runtime payload.

#### Scenario: Toggle requires an explicit boolean request

- **WHEN** a caller omits `enabled` or supplies a non-boolean value
- **THEN** the canonical action rejects the request before loading or changing a schedule row
- **AND** the dashboard API rejects the invalid body with HTTP 422 before calling MCP

#### Scenario: Runtime row applies the requested disabled state

- **WHEN** `schedule_toggle(task_id, enabled=false)` addresses an enabled
  `source='db'` row
- **THEN** the row is atomically persisted as disabled with `next_run_at=NULL`
- **AND** the receipt reports `requested_enabled=false`,
  `observed_enabled=false`, `changed=true`, and `outcome='applied'`

#### Scenario: Retrying an already-landed request is unchanged

- **WHEN** two concurrent or retried calls request the same DB-owned state
- **THEN** at most one call transitions the row
- **AND** every successful receipt reports the same observed requested state
- **AND** a later identical call reports `changed=false` and
  `outcome='already_requested'`

#### Scenario: Re-enabling computes a fresh next occurrence

- **WHEN** `schedule_toggle(task_id, enabled=true)` addresses a disabled
  `source='db'` cron row
- **THEN** the row is enabled with a newly computed non-null `next_run_at`
- **AND** the receipt reports the observed enabled state and that projection

#### Scenario: Missing schedule is typed and side-effect free

- **WHEN** the action addresses an unknown schedule ID
- **THEN** it returns code `SCHEDULE_NOT_FOUND`
- **AND** no schedule row is created or changed

#### Scenario: TOML and other managed schedules are refused

- **WHEN** the action addresses a row whose source is `toml` or another
  non-`db` managed source
- **THEN** it returns `SCHEDULE_TOML_MANAGED` for TOML or
  `SCHEDULE_MANAGED` for the other source
- **AND** it leaves the row unchanged
