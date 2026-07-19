# Core Scheduler — Delta

## MODIFIED Requirements

### Requirement: Tick Handler

The `tick()` function SHALL query all due tasks (`enabled=true AND next_run_at
<= now()`) ordered by `next_run_at`, dispatch each serially, and update
`next_run_at`, `last_run_at`, and `last_result` for every task regardless of
success or failure. A `butler.tick` telemetry span SHALL include `tasks_due` and
`tasks_run`.

Additionally, `tick()` SHALL perform deadline evaluation, event-chain trigger
detection, and a deferred-notification flush. The flush SHALL fetch rows where
`status='pending' AND deliver_at <= now()`, pass a solo row's stored envelope
verbatim to the standard notify delivery function, and update a row to
`delivered` only after successful delivery. It SHALL not re-evaluate
approvals-policy quiet hours or context for a stored envelope; `deliver_at` is
the durable admission decision. Existing same-target coalescing and pending-row
retry behavior SHALL remain unchanged.

The tick span attributes SHALL include `deadlines_evaluated`, `chains_fired`,
and `deferred_flushed` in addition to `tasks_due` and `tasks_run`.

#### Scenario: Due tasks dispatched serially

- **WHEN** `tick()` is called and multiple tasks are due
- **THEN** each task is dispatched one at a time in `next_run_at` order

#### Scenario: Dispatch failure does not block other tasks

- **WHEN** one task dispatch raises an exception
- **THEN** the error is captured in `last_result` and remaining due tasks
  continue

#### Scenario: Deferred notification flush runs each tick

- **WHEN** `tick()` is called
- **THEN** pending deferred notifications with `deliver_at <= now()` are sent
  through the standard delivery path

#### Scenario: Stored owner-default envelope flushes without re-gating

- **WHEN** a due row was parked by an owner-default policy or context hold
- **THEN** the scheduler supplies its stored full envelope to the notifier
  without a second policy/context lookup
- **AND** a transport failure leaves the row pending for the next tick

#### Scenario: Seasonal context remains injected during task dispatch

- **WHEN** `tick()` dispatches a cron or deadline task and active seasons exist
- **THEN** dispatch context includes the active-season metadata

#### Scenario: Legacy schema without until_at continues cron dispatch

- **WHEN** `tick()` runs against a legacy `scheduled_tasks` table without
  `until_at`
- **THEN** due cron tasks continue dispatching with the existing safe fallback
