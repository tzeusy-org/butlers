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

After the switch, prompt-mode dispatch submits an obligation admission instead of invoking a
runtime, and the same check SHALL run in the daemon before that submission, over the
catalog entries the obligation's lead invocation could resolve to. A deferred occurrence
SHALL submit no admission, so it creates no admission decision or goal row and reserves no
budget; the dispatch after the reset SHALL submit that occurrence's admission once, under
the same deduplication as any other occurrence. Once an occurrence is admitted, allowance
SHALL NOT defer, re-admit or resubmit it: exhaustion inside an admitted goal is handled by
invocation selection. Fail-open stays correct because allowance is provider availability,
not a D10 budget or an authority check.

ID: REQ-core-scheduler-004
Source: openspec change provider-allowance-windows (bu-q7vx1q.13); RFC 0038 D5, D10, D16; generalist collision disposition re-scope (bu-q7vx1q.90.2)
Scope: v1-mandatory

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

#### Scenario: A deferred obligation occurrence admits nothing until the reset
- **WHEN** after the switch a prompt-mode occurrence is due and every candidate for its lead
  is on an exhausted account
- **THEN** the daemon SHALL NOT submit an obligation admission, so no admission decision,
  goal row or budget reservation exists for that occurrence
- **AND** the first dispatch after the reset SHALL submit one admission for it, and a
  resubmission of that admission SHALL return the same goal
