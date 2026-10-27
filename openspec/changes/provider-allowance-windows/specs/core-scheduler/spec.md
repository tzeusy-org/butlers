## ADDED Requirements

### Requirement: Allowance Deferral
Before dispatching a due prompt-mode cron task or an unfired deadline threshold, the
scheduler SHALL check whether every enabled, verified catalog entry the task could fall
through to is on an exhausted provider account. When so, it SHALL NOT invoke a runtime: a
cron task SHALL move `next_run_at` to the earliest reset and record
`last_result.outcome='skipped_allowance'` with `deferred_until`; a deadline threshold
SHALL stay unfired so the first tick after the reset dispatches it. The move SHALL be an
optimistic update on the observed `next_run_at` so concurrent ticks defer at most once.
Job-mode tasks SHALL NOT be deferred. A lookup error SHALL fail open.

#### Scenario: Only exhausted accounts defer a due task
- **WHEN** a prompt-mode task is due and every fit account is exhausted until a future reset
- **THEN** the task SHALL NOT be dispatched, its `next_run_at` SHALL equal the earliest
  reset, and its `last_result.outcome` SHALL be `skipped_allowance`

#### Scenario: A free account prevents deferral
- **WHEN** at least one fit catalog entry is on an account that is not exhausted
- **THEN** the task SHALL dispatch normally

#### Scenario: Lapsed exhaustion does not defer
- **WHEN** the exhausted account's reset has passed
- **THEN** the task SHALL dispatch normally
