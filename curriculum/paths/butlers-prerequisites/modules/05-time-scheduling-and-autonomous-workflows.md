# Time, Scheduling, And Autonomous Workflows

Estimated smart-human study time: 7 hours

## Why This Module Matters

Butlers is designed to do recurring and autonomous work. That requires careful reasoning about cron, timezones, retry state, calendar recurrence, projection windows, and event chains.

## Learning Goals

- Understand cron and scheduler advancement semantics.
- Explain timezone-aware datetimes and recurrence boundaries.
- Reason about internal versus provider calendar projections.
- Identify hazards in autonomous task and event-chain changes.

## Subsection: Cron, Scheduler Advancement, And Retry State

### Why This Matters Here

Scheduled tasks can dispatch prompts or jobs. Some failures should advance the schedule, while others should preserve state for retry or diagnosis.

### Technical Deep Dive

A scheduler turns time into work. Cron expressions describe repeating schedules, but production schedulers also need state: last run, next run, failure result, retry policy, source of the task, and whether the task is enabled. The hard part is deciding when to advance `next_run`: advancing after every failure may skip important retries; never advancing can create endless hot loops.

Autonomous systems should make dispatch idempotent where possible. A scheduler tick may crash after creating a session but before updating metadata. Durable task records and clear state transitions make recovery predictable.

### Where It Appears In The Repo

- `docs/runtime/scheduler-execution.md`
- `src/butlers/core/scheduler.py`
- `src/butlers/background.py`
- `roster/*/butler.toml`
- `tests/core/test_core_scheduler.py`
- `tests/daemon/test_scheduler_loop.py`

### Sample Q&A

- Q: Why is scheduler state more than just a cron string?
  A: The system must know last/next run, outcome, source, enablement, and retry behavior.
- Q: Why can advancing after failure be correct for some tasks?
  A: Some failures are terminal for that occurrence; retrying immediately could duplicate side effects or create loops.

### Progress

- [ ] Exposed: I can define cron, tick, next run, dispatch mode, retry state, and task source.
- [ ] Working: I can explain why scheduler advancement rules differ by failure path.
- [ ] Contribution-ready: I can identify tests needed for a scheduler state change.

### Mastery Check

Target level: `contribution-ready`

You should be able to inspect a scheduled task path and predict how success, failure, crash, and retry affect future dispatch.

## Subsection: Timezones, Calendar Projection, And Recurrence

### Why This Matters Here

Calendar events, reminders, scheduled tasks, and workspace views need deterministic interpretation across provider events and internal butler tasks.

### Technical Deep Dive

Time is hard because timestamps have context. A naive datetime lacks timezone. A timezone-aware datetime can be converted, compared, and displayed correctly. Recurrence rules generate many instances from one series, and update/delete semantics may apply to one instance or an entire series.

Projection means materializing events from multiple sources into a unified read model. Provider events, scheduled tasks, and reminders may have different source schemas, but the UI or API wants one calendar view. Projection needs stable origin references, sync cursors, freshness indicators, and conflict-aware upserts.

### Where It Appears In The Repo

- `docs/modules/calendar.md`
- `src/butlers/modules/calendar.py`
- `alembic/versions/core/core_076_calendar_event_columns_and_entities.py`
- `tests/modules/test_module_calendar.py`
- `tests/modules/test_calendar_reminder_integration.py`
- `docs/frontend/backend-api-contract.md`

### Sample Q&A

- Q: Why is a recurring provider event not the same thing as many independent events?
  A: It has series identity and instance-specific semantics; updates may target one occurrence or the series.
- Q: Why does projection need an `origin_ref`-style linkage?
  A: It lets sync refresh the same materialized row deterministically instead of creating duplicates.

### Progress

- [ ] Exposed: I can define timezone-aware datetime, recurrence, instance, series, projection, cursor, and freshness.
- [ ] Working: I can explain why provider and internal calendar sources need normalization.
- [ ] Contribution-ready: I can identify duplicate-instance or stale-projection risks in a calendar change.

### Mastery Check

Target level: `contribution-ready`

You should be able to reason about how a reminder, scheduled task, or provider event becomes a calendar workspace row and what happens on sync.

## Module Mastery Gate

- [ ] I can explain scheduler state transitions and cron advancement.
- [ ] I can distinguish timezone-aware and naive time handling.
- [ ] I can describe projection from internal/provider sources.
- [ ] I can identify recurrence or duplicate-projection hazards before changing calendar code.
