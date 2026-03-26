# Task Scheduler

## Purpose
Provides cron-driven task dispatch for butlers, supporting TOML-configured and runtime-created scheduled tasks with deterministic staggering, dual dispatch modes (prompt and job), auto-disable boundaries, calendar projection fields, and a one-shot `remind` tool.

## ADDED Requirements

### Requirement: Cron Evaluation and next_run_at Computation
All cron expressions are 5-field format (minute hour day month day-of-week) evaluated in UTC. The `croniter` library validates and computes next occurrences. The `timezone` field is informational for projection/display only and does not affect cron evaluation.

#### Scenario: Valid cron expression
- **WHEN** a schedule is created with a valid 5-field cron expression
- **THEN** `croniter.is_valid(cron)` passes and `next_run_at` is computed as the next UTC occurrence

#### Scenario: Invalid cron expression
- **WHEN** a schedule is created or updated with an invalid cron expression
- **THEN** a `ValueError` is raised with a descriptive message

### Requirement: Dispatch Modes
Scheduled tasks support two dispatch modes: `prompt` (sends text to the LLM CLI spawner) and `job` (sends a structured job name and optional arguments). Mode-specific constraints are enforced: prompt mode requires non-empty `prompt` and forbids `job_name`/`job_args`; job mode requires non-empty `job_name` and forbids `prompt`.

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
When multiple tasks share the same cron cadence, a deterministic hash-based offset disperses their dispatch times across the cron interval. The offset is computed via SHA-256 of the `stagger_key`, capped at `min(max_stagger_seconds, cadence - 1)`, defaulting to 900 seconds (15 minutes) maximum.

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
At daemon startup, `sync_schedules()` reconciles `[[butler.schedule]]` TOML entries with the `scheduled_tasks` DB table. Matching is by `name` field. New entries are inserted with `source='toml'`, changed entries are updated, and TOML tasks removed from config are disabled (not deleted) to preserve history.

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
The `tick()` function queries all due tasks (`enabled=true AND next_run_at <= now()`) ordered by `next_run_at`, dispatches each serially, and updates `next_run_at`, `last_run_at`, and `last_result` for every task regardless of success or failure. A telemetry span `butler.tick` is created with `tasks_due` and `tasks_run` attributes.

Additionally, `tick()` SHALL perform three new evaluation passes:

1. **Deadline evaluation**: For each enabled task with `task_type='deadline'`, compute `days_remaining` and evaluate alert thresholds. Dispatch deadline tasks whose thresholds are newly satisfied.
2. **Event chain trigger detection**: Query calendar projection for events whose `end_at` has passed since last tick. Check for deadline status transitions. Fire matching event chains by materializing their actions as one-shot scheduled tasks.
3. **Deferred notification flush**: Query `deferred_notifications` where `status='pending' AND deliver_at <= now()` and deliver each via the standard notify pipeline.

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

#### Scenario: Seasonal context injected during dispatch
- **WHEN** `tick()` dispatches any task (cron or deadline)
- **AND** `get_active_seasons()` returns non-empty results
- **THEN** the dispatch context includes `active_seasons` metadata

### Requirement: Auto-Disable via until_at Boundary
When a task has `until_at` set and the computed `next_run_at` exceeds it, the task is automatically set to `enabled=false` and `next_run_at=NULL` after its final dispatch.

#### Scenario: Task auto-disables after boundary
- **WHEN** a task fires and the next computed `next_run_at` is after `until_at`
- **THEN** the task is set to `enabled=false` and `next_run_at=NULL`

### Requirement: Schedule CRUD API
Runtime schedule management via `schedule_create`, `schedule_update`, `schedule_delete`, and `schedule_list`.

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
The `remind` MCP tool creates one-shot scheduled tasks by generating a cron expression for a target time and setting `until_at` to auto-disable after firing. Supports `delay_minutes` (relative) and `remind_at` (absolute) timing with mutual exclusivity.

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
Scheduled tasks carry optional fields for calendar module integration: `timezone`, `start_at`, `end_at`, `until_at`, `display_title`, `calendar_event_id`. These are validated on create/update (timezone-aware datetimes required, `end_at > start_at`, `until_at >= start_at`).

#### Scenario: Projection fields validated
- **WHEN** a schedule is created with `start_at` as a naive (non-timezone-aware) datetime
- **THEN** a `ValueError` is raised requiring timezone-aware datetimes
