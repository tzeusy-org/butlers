# Task Scheduler

## Purpose
Provides cron-driven task dispatch for butlers, supporting TOML-configured and runtime-created scheduled tasks with deterministic staggering, dual dispatch modes (prompt and job), auto-disable boundaries, calendar projection fields, and a one-shot `remind` tool.

## Requirements

### Requirement: Cron Evaluation and next_run_at Computation

All cron expressions SHALL use 5-field format (minute hour day month day-of-week).
During daemon startup synchronization, `sync_schedules()` SHALL calculate
`next_run_at` for a new or changed TOML schedule using the startup-resolved
owner general timezone. Startup synchronization does not read the stored per-row
`timezone` value, and an otherwise unchanged TOML row retains its existing
`next_run_at` even if the owner timezone has changed.

During `tick()` evaluation, cron fields SHALL use an effective timezone: the
scheduler loop's resolved owner general timezone is the default; a stored
`timezone` of `UTC`, `NULL`, or empty is a default sentinel that follows that
default; and a non-UTC `timezone` is an explicit per-schedule override.
If the owner default or effective timezone is invalid, the scheduler SHALL fall
back to UTC. The `croniter` library validates and computes the next occurrence
in the applicable timezone; the scheduler SHALL convert the occurrence to UTC
for `next_run_at` and then apply deterministic staggering as specified below.

#### Scenario: Valid cron expression
- **WHEN** `sync_schedules()` creates or updates a TOML schedule with a valid
  5-field cron expression
- **THEN** `croniter.is_valid(cron)` passes and `next_run_at` is computed from
  the next occurrence in the startup-resolved owner-general timezone, converted to UTC,
  and subject to deterministic staggering
- **AND WHEN** `tick()` computes a subsequent `next_run_at`
- **THEN** it uses that stored row's effective timezone, converted to UTC and
  subject to deterministic staggering

#### Scenario: Startup synchronization uses the owner default
- **WHEN** a daemon creates or updates a TOML schedule during
  `sync_schedules()`
- **THEN** the cron fields SHALL be interpreted in the startup-resolved
  owner-general timezone
- **AND** startup synchronization SHALL NOT inspect a retained per-row
  `timezone` override when computing that row's replacement `next_run_at`
- **AND** if the owner timezone cannot be resolved, the cron fields SHALL be
  interpreted in UTC

#### Scenario: Unchanged TOML rows retain their prior startup computation
- **WHEN** a TOML schedule's synced fields are unchanged on a later daemon
  startup, including after an owner timezone change
- **THEN** `sync_schedules()` SHALL leave that row's existing `next_run_at`
  unchanged

#### Scenario: Tick-time default schedule timezone follows the owner
- **WHEN** `tick()` evaluates a schedule whose stored `timezone` is `UTC`,
  `NULL`, or empty
- **THEN** the cron fields SHALL be interpreted in the scheduler loop's
  resolved owner-general timezone
- **AND** if that timezone cannot be resolved, they SHALL be interpreted in UTC

#### Scenario: A running scheduler loop retains its resolved owner default
- **WHEN** the owner general timezone changes while a scheduler loop is running
- **THEN** subsequent `tick()` calls in that loop SHALL continue using the
  owner-general default resolved when the loop started
- **AND** the changed owner timezone SHALL take effect after the next daemon
  restart starts a new scheduler loop

#### Scenario: Explicit schedule timezone overrides the owner default
- **WHEN** `tick()` evaluates a schedule with a non-UTC stored `timezone`
- **THEN** the cron fields SHALL be interpreted in that timezone rather than the
  owner's configured general timezone

#### Scenario: Invalid cron expression
- **WHEN** a schedule is created or updated with an invalid cron expression
- **THEN** a `ValueError` is raised with a descriptive message

### Requirement: Dispatch Modes
Scheduled tasks SHALL support two dispatch modes: `prompt` (sends text to the LLM CLI spawner) and `job` (sends a structured job name and optional arguments). Mode-specific constraints are enforced: prompt mode requires non-empty `prompt` and forbids `job_name`/`job_args`; job mode requires non-empty `job_name` and forbids `prompt`.

Prompt-mode dispatch SHALL pass the task's `complexity` field through to the spawner's `trigger()` call.

#### Scenario: Prompt mode dispatch
- **WHEN** a due task has `dispatch_mode='prompt'`
- **THEN** the dispatch function is called with `prompt=<text>`, `trigger_source="schedule:<task-name>"`, and `complexity=<task-complexity>`

#### Scenario: Job mode dispatch
- **WHEN** a due task has `dispatch_mode='job'`
- **THEN** the dispatch function is called with `job_name=<name>`, `job_args=<dict|None>`, and `trigger_source="schedule:<task-name>"`

#### Scenario: Invalid dispatch mode combination
- **WHEN** a task is created with `dispatch_mode='prompt'` but no prompt text
- **THEN** a `ValueError` is raised requiring a non-empty prompt

### Requirement: Deterministic Staggering
When multiple tasks share the same cron cadence, a deterministic hash-based offset SHALL disperse their dispatch times across the cron interval. The offset is computed via SHA-256 of the `stagger_key`, capped at `min(max_stagger_seconds, cadence - 1)`, defaulting to 900 seconds (15 minutes) maximum.

#### Scenario: Same key produces same offset
- **WHEN** `_stagger_offset_seconds()` is called twice with the same `stagger_key` and cron
- **THEN** both calls return the same offset value

#### Scenario: Offset never exceeds cadence
- **WHEN** a task has a cron cadence of N seconds
- **THEN** the stagger offset is strictly less than N seconds

#### Scenario: No staggering when key is absent
- **WHEN** `stagger_key` is `None` or empty
- **THEN** no offset is applied to `next_run_at`

### Requirement: TOML-to-DB Schedule Synchronization
At daemon startup, `sync_schedules()` SHALL reconcile `[[butler.schedule]]` TOML entries with the `scheduled_tasks` DB table. Matching is by `name` field. New entries are inserted with `source='toml'`, changed entries are updated, and TOML tasks removed from config are disabled (not deleted) to preserve history.

The `complexity` field is included in the sync comparison and persisted alongside other schedule fields.

TOML schedule entries MAY now include `task_type = "deadline"` with associated deadline fields (`target_date`, `lead_time_days`, `alert_thresholds`). These are synced alongside cron-type schedules.

#### Scenario: New TOML schedule inserted
- **WHEN** a TOML schedule entry has no matching row in DB
- **THEN** a new row is inserted with `source='toml'`, `enabled=true`, computed `next_run_at`, and `complexity` from TOML (default `medium`)

#### Scenario: New TOML deadline inserted
- **WHEN** a TOML schedule entry has `task_type = "deadline"` with `target_date`, `lead_time_days`, and `alert_thresholds`
- **THEN** a new row is inserted with `task_type='deadline'` and deadline metadata

#### Scenario: Changed TOML schedule updated
- **WHEN** a TOML schedule entry's cron, prompt, dispatch_mode, job_name, job_args, or complexity differ from the DB row
- **THEN** the DB row is updated and `next_run_at` is recomputed

#### Scenario: Removed TOML schedule disabled
- **WHEN** a DB row with `source='toml'` has no matching TOML entry
- **THEN** the row is set to `enabled=false` (not deleted)

### Requirement: Tick Handler
The `tick()` function SHALL query all due tasks (`enabled=true AND next_run_at <= now()`) ordered by `next_run_at`, dispatch each serially, and update `next_run_at`, `last_run_at`, and `last_result` for every task regardless of success or failure. A telemetry span `butler.tick` is created with `tasks_due` and `tasks_run` attributes.

Additionally, `tick()` SHALL perform three new evaluation passes:

1. **Deadline evaluation**: For each enabled task with `task_type='deadline'`, compute `days_remaining` and evaluate alert thresholds. Dispatch deadline tasks whose thresholds are newly satisfied.
2. **Event chain trigger detection**: Query calendar projection for events whose `end_at` has passed since last tick. Check for deadline status transitions. Fire matching event chains by materializing their actions as one-shot scheduled tasks.
3. **Deferred notification flush**: Query `deferred_notifications` where `status='pending' AND deliver_at <= now()`, pass a solo row's stored envelope verbatim to the standard notify pipeline, and update a row to delivered only after successful delivery. The flush SHALL NOT re-evaluate approvals-policy quiet hours or context for a stored envelope; `deliver_at` is the durable admission decision. Existing same-target coalescing and pending-row retry behavior remain unchanged.

The tick span attributes SHALL include `deadlines_evaluated`, `chains_fired`, and `deferred_flushed` in addition to the existing `tasks_due` and `tasks_run`.

#### Scenario: Due tasks dispatched serially
- **WHEN** `tick()` is called and multiple tasks are due
- **THEN** each task is dispatched one at a time in `next_run_at` order

#### Scenario: Dispatch failure does not block other tasks
- **WHEN** one task's dispatch raises an exception
- **THEN** the error is captured in `last_result` as `{"error": "..."}` and the remaining due tasks continue dispatching
- **AND** the failed task's `next_run_at` is still advanced to the next cron occurrence

#### Scenario: Deadline evaluation runs each tick
- **WHEN** `tick()` is called
- **THEN** all enabled `task_type='deadline'` tasks are evaluated for threshold crossing
- **AND** newly satisfied thresholds trigger dispatch with deadline context

#### Scenario: Event chain detection runs each tick
- **WHEN** `tick()` is called
- **THEN** calendar projection is checked for recently ended events
- **AND** deadline status transitions are checked
- **AND** matching event chains with `status='active'` are fired

#### Scenario: Deferred notification flush runs each tick
- **WHEN** `tick()` is called
- **THEN** pending deferred notifications with `deliver_at <= now()` are delivered

#### Scenario: Stored owner-default envelope flushes without re-gating
- **WHEN** a due row was parked by an owner-default policy or context hold
- **THEN** the scheduler supplies its stored full envelope to the notifier
  without a second policy/context lookup
- **AND** a transport failure leaves the row pending for the next tick

#### Scenario: Seasonal context injected during dispatch
- **WHEN** `tick()` dispatches any task (cron or deadline)
- **AND** `get_active_seasons()` returns non-empty results
- **THEN** the dispatch context includes `active_seasons` metadata

#### Scenario: Legacy schema without until_at continues cron dispatch
- **WHEN** `tick()` runs against a deployed legacy `scheduled_tasks` table that does not yet include the `until_at` column
- **THEN** it logs a warning that `scheduled_tasks.until_at` is missing
- **AND** the due-task query projects `NULL::timestamptz AS until_at`
- **AND** due cron tasks continue dispatching without applying an auto-disable boundary until the schema is backfilled

### Requirement: Auto-Disable via until_at Boundary
When a task has `until_at` set and the computed `next_run_at` exceeds it, the scheduler SHALL automatically set the task to `enabled=false` and `next_run_at=NULL` after its final dispatch.

#### Scenario: Task auto-disables after boundary
- **WHEN** a task fires and the next computed `next_run_at` is after `until_at`
- **THEN** the task is set to `enabled=false` and `next_run_at=NULL`

### Requirement: Schedule CRUD API
Runtime schedule management SHALL be exposed via `schedule_create`, `schedule_update`, `schedule_delete`, and `schedule_list`.

The CRUD API SHALL accept `task_type` as a parameter. When `task_type='deadline'`, deadline-specific fields (`target_date`, `lead_time_days`, `alert_thresholds`, `deadline_status`) are required on create and accepted on update. When `task_type='cron'` (default), existing behavior is unchanged.

#### Scenario: Create runtime schedule
- **WHEN** `schedule_create()` is called with valid parameters
- **THEN** a new row is inserted with `source='db'`, `enabled=true`, and computed `next_run_at`
- **AND** the new task's UUID is returned

#### Scenario: Create deadline via schedule API
- **WHEN** `schedule_create(task_type="deadline", target_date="2026-08-15", lead_time_days=42, alert_thresholds=[...])` is called
- **THEN** a deadline task is created with appropriate metadata
- **AND** `next_run_at` is computed based on the first alert threshold

#### Scenario: Duplicate name rejected
- **WHEN** `schedule_create()` is called with an existing task name
- **THEN** a `ValueError` is raised

#### Scenario: Update schedule fields
- **WHEN** `schedule_update()` is called with allowed fields
- **THEN** the specified fields are updated atomically
- **AND** if `cron` changes, `next_run_at` is recomputed
- **AND** if `enabled` is set to False, `next_run_at` is set to NULL

#### Scenario: Delete runtime schedule
- **WHEN** `schedule_delete()` is called for a `source='db'` task
- **THEN** the row is removed

#### Scenario: Cannot delete TOML schedule
- **WHEN** `schedule_delete()` is called for a `source='toml'` task
- **THEN** a `ValueError` is raised

### Requirement: Remind Tool
The `remind` MCP tool SHALL create one-shot scheduled tasks by generating a cron expression for a target time and setting `until_at` to auto-disable after firing. It supports `delay_minutes` (relative) and `remind_at` (absolute) timing with mutual exclusivity.

#### Scenario: Reminder created with delay
- **WHEN** `remind(message, channel, delay_minutes=60)` is called
- **THEN** a scheduled task is created with a cron matching `now + 60 minutes` and `until_at = target + 1 minute`

#### Scenario: Invalid timing parameters
- **WHEN** both `delay_minutes` and `remind_at` are provided
- **THEN** an error response is returned

### Requirement: Scheduled Task Complexity Field
Scheduled tasks SHALL support an optional `complexity` field that specifies the complexity tier for spawned sessions.

#### Scenario: Complexity in TOML schedule
- **WHEN** a `[[butler.schedule]]` entry includes `complexity = "high"`
- **THEN** sessions spawned by this task use complexity `high` for model resolution

#### Scenario: Complexity default
- **WHEN** a `[[butler.schedule]]` entry omits the `complexity` field
- **THEN** the complexity defaults to `medium`

#### Scenario: Invalid complexity value
- **WHEN** a `[[butler.schedule]]` entry includes `complexity = "invalid"`
- **THEN** a `ValueError` is raised listing valid complexity values

#### Scenario: Complexity in scheduled_tasks DB table
- **WHEN** the `scheduled_tasks` table schema is defined
- **THEN** it includes a `complexity` column (text, nullable, default `'medium'`)

#### Scenario: Complexity in schedule CRUD
- **WHEN** `schedule_create()` or `schedule_update()` is called
- **THEN** the `complexity` field is accepted as an allowed parameter
- **AND** valid values are: `trivial`, `medium`, `high`, `extra_high`

### Requirement: Calendar Projection Fields
Scheduled tasks SHALL carry optional fields for calendar module integration: `timezone`, `start_at`, `end_at`, `until_at`, `display_title`, `calendar_event_id`. These are validated on create/update (timezone-aware datetimes required, `end_at > start_at`, `until_at >= start_at`).

#### Scenario: Projection fields validated
- **WHEN** a schedule is created with `start_at` as a naive (non-timezone-aware) datetime
- **THEN** a `ValueError` is raised requiring timezone-aware datetimes

#### Scenario: Existing scheduler tables backfill projection fields
- **WHEN** core migrations run against an existing `scheduled_tasks` table that predates the calendar projection fields
- **THEN** the table includes `timezone`, `start_at`, `end_at`, `until_at`, `display_title`, and `calendar_event_id`
- **AND** the scheduler window and `until_at` bounds constraints are present
- **AND** a partial unique index enforces non-null `calendar_event_id` uniqueness

### Requirement: Task Continuity Ledger with Role-Fit Registration (Opt-In)
A recurring PROMPT-mode scheduled task MAY opt in to carrying forward what its previous run concluded, via a `continuity` boolean field (TOML `[[butler.schedule]]` entry, or `schedule_create`/`schedule_update` — PROMPT mode only; setting `continuity=true` with `dispatch_mode="job"` SHALL raise a `ConfigError`). `sync_schedules()` SHALL persist the field to `scheduled_tasks.continuity` (migration `core_229`).

When a session concludes, it MAY call the `carry_forward(task_name, content)` core tool to record its own conclusion. `record_carry_forward()` SHALL write to `public.task_continuity` in a transaction that archives (sets `is_live=false` on) any other live row for that `(butler_name, task_name)` before inserting the new live row, so at most one row is live per butler+task at a time. A repeated call within the same session (same `session_id`) SHALL update the existing row for that session rather than creating a duplicate (`ON CONFLICT (butler_name, task_name, session_id) DO UPDATE`).

The `carry_forward` core tool SHALL NOT be registered for staffer-typed butlers (`ButlerType.STAFFER`: concierge, messenger, switchboard, qa), matching the existing non-STAFFER scoping already drawn around other narrative/dispatch-adjacent core tools (e.g. `notify`, the `temporal` group). `continuity=true` is restricted to PROMPT-mode schedules, and staffer butlers' schedules are exclusively job-mode deterministic handlers; the tool would have no reachable caller there. This is a role-fit and reachability constraint, not a registered-handler budget decision. RFC 0002 Amendment 1 and RFC 0027 apply the 30-50 target to full definitions initially loaded into model context, not to the canonical registered set; presentation discovery, admission, and initial-schema-byte evidence remain outside the scheduler contract and owned by `bu-ondtw`.

`tick()` SHALL, only for tasks with `continuity=true`, read the current live row via `fetch_live_carry_forward()` after preparing the dispatched prompt and append a continuity block to it:
- naming the previous run's `session_id`, `recorded_at`, and age, plus the recorded `content`, when a live row exists;
- the literal text "the last run recorded no carry-forward" when no live row exists for that task (never fabricated content).

Continuity lookup SHALL be fail-open, not fail-closed: any exception during lookup SHALL be caught and logged, and the tick SHALL proceed by omitting the continuity block rather than failing the dispatch. This is deliberately the opposite of the blind-spot preamble's fail-closed contract (see core-spawner spec, Blind-Spot Preamble Injection) — continuity is an additive convenience, and a task that has always run without it must keep running unaffected by a lookup failure.

Tasks that do not opt in (`continuity=false`, the default) SHALL see no behavior change: `tick()` SHALL NOT query `task_continuity` for them and SHALL NOT alter their dispatched prompt.

#### Scenario: Opted-in task with no prior run gets an honest empty state
- **WHEN** a `continuity=true` task fires for the first time (no live row in `public.task_continuity`)
- **THEN** the dispatched prompt includes a continuity block reading "the last run recorded no carry-forward"

#### Scenario: Opted-in task with a prior conclusion carries it forward
- **WHEN** a `continuity=true` task's previous run called `carry_forward(task_name, content)`
- **AND** the task fires again
- **THEN** the dispatched prompt includes a continuity block naming the previous session_id, recorded_at, age, and content

#### Scenario: New session supersedes the previous live row
- **WHEN** a task calls `carry_forward()` from a new session
- **THEN** the prior live row for that `(butler_name, task_name)` is archived (`is_live=false`)
- **AND** the new row becomes the sole live row

#### Scenario: Same-session retry deduplicates instead of superseding
- **WHEN** a session calls `carry_forward()` more than once with the same `session_id`
- **THEN** the existing row for that session is updated in place
- **AND** no additional row is archived

#### Scenario: Non-opted-in tasks are unaffected
- **WHEN** a task has `continuity=false` (the default) or omits the field
- **THEN** `tick()` SHALL NOT call `fetch_live_carry_forward()` for that task
- **AND** its dispatched prompt is byte-identical to the pre-continuity behavior

#### Scenario: Continuity lookup failure does not block dispatch
- **WHEN** `fetch_live_carry_forward()` raises for an opted-in task
- **THEN** the exception is logged and the tick proceeds
- **AND** the dispatched prompt omits the continuity block entirely (not a fail-closed placeholder)

#### Scenario: Staffer butlers do not receive the carry_forward tool
- **WHEN** a staffer-typed butler (`ButlerType.STAFFER`) registers its core tools
- **THEN** `carry_forward` is NOT among them
- **AND** this does not affect any other butler's tool registration

#### Scenario: Legacy schema without the continuity column
- **WHEN** `tick()` runs against a `scheduled_tasks` table that predates migration `core_229`
- **THEN** the column probe treats `continuity` as absent
- **AND** every task is treated as `continuity_enabled=False`, with no behavior change from before the ledger existed

#### Scenario: The ledger is excluded from the nightly backup, by design
- **WHEN** the nightly `pg_dump` backup runs
- **THEN** `public.task_continuity` is excluded (its RLS is FORCEd, like `public.expected_signals`, and pg_dump cannot read a FORCE-RLS table as the table owner without `--enable-row-security`, which this backup deliberately never sets)
- **AND** a restored database has no live carry-forward rows
- **AND** the next run of any opted-in task reports "the last run recorded no carry-forward" rather than failing or fabricating a conclusion
