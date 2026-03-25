## Context

Butlers currently use cron-driven scheduling for all time-based work. The `core-scheduler` evaluates 5-field cron expressions in UTC, dispatches due tasks, and advances `next_run_at`. The `module-calendar` provides event CRUD, sync, and projection. The `core-notify` tool routes outbound notifications through the Switchboard to the Messenger butler.

This infrastructure handles periodic triggers well but lacks three temporal primitives users need: (1) countdown-based deadlines that compute urgency from a target date, (2) event-driven chains that trigger workflows after something happens, and (3) contextual time awareness (seasons, quiet hours) that shapes when and how butlers act.

The user expects butlers to track "visa expires August 15 -- start renewal 6 weeks before" or "after the dentist appointment, log the visit" without manually computing cron schedules.

## Goals / Non-Goals

**Goals:**
- Extend `scheduled_tasks` with deadline metadata (target_date, lead_time, alert_thresholds) so the existing tick loop evaluates deadlines alongside cron tasks
- Define event-chain triggers that fire post-event workflows, integrated with calendar event completion signals
- Provide seasonal/cyclical period definitions that butlers can query to adjust behavior
- Enforce quiet hours and notification batching in the notify pipeline, respecting user timezone
- Expose MCP tools for deadline, event-chain, and delivery-preference management
- Keep all temporal features as extensions of existing core infrastructure -- no parallel dispatch loops

**Non-Goals:**
- Replacing cron scheduling -- cron remains the primitive for periodic tasks
- Complex workflow orchestration (DAGs, branching, retries) -- event chains are linear trigger sequences
- Multi-user timezone support -- single-owner timezone per butler instance
- Natural language date parsing -- callers provide structured dates; NLP is the LLM's responsibility
- Calendar provider webhooks for real-time event completion -- polling-based detection is sufficient for v1

## Decisions

### Decision 1: Deadlines as extended scheduled_tasks, not a separate table

Deadlines reuse the `scheduled_tasks` table with new nullable columns: `task_type` (enum: `cron`, `deadline`), `target_date`, `lead_time_days`, `alert_thresholds` (JSONB array of `{days_before, severity}`), and `deadline_status` (enum: `pending`, `alerted`, `escalated`, `completed`, `expired`).

**Rationale:** The tick loop already queries due tasks and dispatches them. Adding deadline evaluation to the same loop avoids a parallel dispatch mechanism. Deadlines share the same dispatch modes (prompt/job), staggering, and CRUD API. A separate table would duplicate dispatch, logging, and CRUD infrastructure.

**Alternative considered:** Separate `deadlines` table with its own tick function. Rejected because it doubles the dispatch surface area and the two would inevitably need to interact (deadline fires a scheduled task).

### Decision 2: Countdown computation in tick()

The `tick()` function gains a deadline evaluation pass: for each `task_type='deadline'` task, compute `days_remaining = (target_date - now()).days`. If `days_remaining` matches any alert threshold in `alert_thresholds`, dispatch the task with threshold metadata in the prompt/job context. The task's `deadline_status` transitions through states as thresholds are crossed.

**Rationale:** Centralizing countdown logic in `tick()` means deadlines benefit from existing telemetry spans, error handling, and serial dispatch ordering. The countdown is recomputed on every tick rather than stored, avoiding drift.

**Alternative considered:** Pre-computing all alert times as separate scheduled_tasks rows. Rejected because threshold changes would require row manipulation and the number of rows would multiply per deadline.

### Decision 3: Event chains as a separate table with FK to scheduled_tasks

Event chains live in a new `event_chains` table: `id`, `name`, `trigger_type` (enum: `calendar_event_end`, `deadline_passed`, `deadline_threshold`), `trigger_reference` (event_id or deadline task_id), `actions` (JSONB array of `{action_type, delay_minutes, prompt, job_name, job_args}`), `status`, `butler_name`. When a trigger fires, chain actions are materialized as one-shot scheduled_tasks entries with appropriate delays.

**Rationale:** Event chains need their own identity (name, status, trigger binding) that doesn't map to a single scheduled_task. But their actions become scheduled_tasks, reusing the dispatch pipeline. This avoids building a second execution engine.

**Alternative considered:** Encoding chains as metadata on calendar events. Rejected because chains may trigger from non-calendar sources (deadline thresholds) and calendar events are provider-synced external objects.

### Decision 4: Seasonal periods as butler-scoped configuration

Seasonal periods are defined in a `seasonal_periods` table: `id`, `name`, `period_type` (enum: `annual`, `academic`, `fiscal`, `custom`), `start_month`, `start_day`, `end_month`, `end_day`, `timezone`, `metadata` (JSONB for custom attributes), `butler_name`. A `get_active_seasons()` query returns currently active periods. Butlers query this during prompt construction to inject seasonal context.

**Rationale:** Seasons are relatively static configuration (tax season is always Jan-Apr, academic terms follow a known calendar). A simple table with month/day ranges and an active-period query is sufficient. No complex recurrence logic needed -- these are annual cycles.

**Alternative considered:** Encoding seasons in butler.toml. Rejected because seasons may need runtime updates (user moves to a different academic calendar) and TOML changes require daemon restart.

### Decision 5: Quiet hours as a delivery gate in core-notify

Quiet hours are enforced in the `notify()` tool's delivery pipeline before envelope construction. A `delivery_preferences` table stores per-butler settings: `quiet_hours_start` (time), `quiet_hours_end` (time), `timezone`, `batch_low_priority` (boolean), `batch_delivery_time` (time, default 07:00). When a notification arrives during quiet hours, high-priority notifications are delivered immediately; low/medium are deferred to a `deferred_notifications` table and dispatched at `batch_delivery_time`.

**Rationale:** Gating at the notify tool level (before Switchboard routing) keeps the logic in one place and applies to all channels uniformly. The Messenger butler doesn't need to know about quiet hours.

**Alternative considered:** Gating at the Messenger level. Rejected because the Messenger serves multiple butlers and quiet hours are per-butler preferences. Each butler's notify tool is the right enforcement point.

### Decision 6: Calendar event completion detection via projection diff

Event chain triggers of type `calendar_event_end` are detected by comparing the calendar projection's event end times against the current time during `tick()`. When an event's `end_at` has passed and the event hasn't been processed for chain triggers, any matching chains fire.

**Rationale:** The calendar module already maintains a projection table with event times. Querying "events that ended since last tick" is a simple SQL query. This avoids adding webhook infrastructure or polling the provider separately.

**Alternative considered:** Google Calendar push notifications. Rejected because it requires public webhook endpoints, is provider-specific, and adds infrastructure complexity disproportionate to v1 needs.

## Risks / Trade-offs

- **[Risk] Tick interval determines deadline precision** -- Deadlines are evaluated per-tick (typically every minute). A deadline with an alert threshold of "1 day before" will fire within the tick interval of the computed time, not at exactly 24 hours before. -> Mitigation: Document that deadline alerts are approximate to the tick interval. For minute-level precision, use the existing `remind` tool instead.

- **[Risk] Event chain actions could cascade** -- A chain action that triggers another deadline or event could create unbounded cascading. -> Mitigation: Cap chain depth at 3 levels. Chain actions cannot themselves define new event chains.

- **[Risk] Quiet hours deferral could lose notifications** -- If the daemon restarts, deferred notifications in the `deferred_notifications` table must survive. -> Mitigation: Deferred notifications are persisted to the database, not held in memory. The tick loop includes a deferred-notification flush pass.

- **[Risk] Seasonal period overlap** -- Two seasonal periods can overlap (tax season and academic spring term). -> Mitigation: `get_active_seasons()` returns all active periods; butlers handle multiple concurrent seasons in their prompt context.

- **[Trade-off] No real-time event completion** -- Event chain triggers depend on the tick interval for detection, introducing up to 1 minute of latency after an event ends. Acceptable for v1 given the alternative (webhook infrastructure).

- **[Trade-off] Single timezone per butler** -- Quiet hours and seasonal periods use a single timezone per butler instance. Multi-timezone users need multiple butler instances or manual override. Acceptable for the user-federated model where each instance serves one owner.

## Migration Plan

1. **Database migration**: Alembic migration adds new columns to `scheduled_tasks`, creates `event_chains`, `seasonal_periods`, `delivery_preferences`, and `deferred_notifications` tables. All new columns are nullable; existing tasks continue working as `task_type='cron'` (default).
2. **Scheduler extension**: `tick()` gains deadline evaluation and deferred-notification flush. Existing cron dispatch is unaffected.
3. **Notify extension**: Quiet hours gate added before envelope construction. Existing notify flow is unaffected when no delivery preferences are configured.
4. **New MCP tools**: Added incrementally -- deadline CRUD, event chain CRUD, seasonal period CRUD, delivery preference management.
5. **Rollback**: Drop new tables and columns. Existing `task_type='cron'` tasks and notify flow are unchanged.

## Open Questions

- Should deadline alert thresholds support time-of-day targeting (e.g., "alert 7 days before, at 9am local")? Current design fires on the first tick after the threshold is crossed.
- Should event chains support conditional actions (e.g., "only fire if the calendar event had attendees")? Current design is unconditional linear sequences.
