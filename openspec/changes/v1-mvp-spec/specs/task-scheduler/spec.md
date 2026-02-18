# Task Scheduler

The task scheduler is a core component present in every butler. It manages scheduled tasks backed by a PostgreSQL table and dispatches prompts to the LLM CLI spawner on a cron-driven cadence. Tasks can originate from the butler's `butler.toml` configuration (TOML-source) or be created at runtime via MCP tools (DB-source).

The scheduler exposes four MCP tools (`schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`) and a `tick()` entry point called by the Heartbeat butler.

## Database Schema

```sql
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    cron TEXT NOT NULL,
    prompt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'db',   -- 'toml' or 'db'
    enabled BOOLEAN NOT NULL DEFAULT true,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    last_result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## ADDED Requirements

### Requirement: TOML bootstrap sync on startup

The scheduler SHALL synchronize `[[butler.schedule]]` entries from `butler.toml` into the `scheduled_tasks` table on butler startup. Each TOML schedule entry MUST specify a `name`, `cron`, and `prompt`. Synced rows SHALL have `source='toml'`.

If a TOML task name already exists in the database, the scheduler SHALL update the `cron` and `prompt` columns to match the TOML values and SHALL set `updated_at` to the current time.

If a database row with `source='toml'` has a `name` that no longer appears in the TOML configuration, the scheduler SHALL set `enabled=false` on that row. The row MUST NOT be deleted.

After syncing, the scheduler SHALL compute and persist `next_run_at` for every task that has `enabled=true` and a `NULL` `next_run_at`, using `croniter.get_next()` from the current time.

#### Scenario: First startup with TOML schedule entries

WHEN the butler starts for the first time
AND `butler.toml` contains two `[[butler.schedule]]` entries named "daily-review" and "weekly-summary"
THEN the `scheduled_tasks` table SHALL contain exactly two rows
AND both rows SHALL have `source='toml'` and `enabled=true`
AND both rows SHALL have a non-null `next_run_at` computed from their cron expressions
AND both rows SHALL have `last_run_at` as NULL.

#### Scenario: TOML entry updated between restarts

WHEN the butler starts
AND the database contains a task with `name='daily-review'`, `source='toml'`, and `cron='0 9 * * *'`
AND `butler.toml` now defines "daily-review" with `cron='0 8 * * *'`
THEN the row for "daily-review" SHALL have `cron='0 8 * * *'`
AND `updated_at` SHALL be updated to the current time
AND `next_run_at` SHALL be recomputed based on the new cron expression.

#### Scenario: TOML entry removed between restarts

WHEN the butler starts
AND the database contains a task with `name='old-task'` and `source='toml'`
AND `butler.toml` no longer contains a `[[butler.schedule]]` entry named "old-task"
THEN the row for "old-task" SHALL have `enabled=false`
AND the row MUST NOT be deleted from the database.

#### Scenario: DB-source tasks are not affected by TOML sync

WHEN the butler starts
AND the database contains a task with `name='custom-task'` and `source='db'`
AND `butler.toml` does not contain an entry named "custom-task"
THEN the row for "custom-task" SHALL remain unchanged.

### Requirement: Runtime task creation via MCP tool

The `schedule_create` MCP tool SHALL accept `name`, `cron`, and `prompt` parameters and insert a new row into `scheduled_tasks` with `source='db'` and `enabled=true`.

The tool SHALL validate the `cron` parameter using `croniter.is_valid()` (or equivalent). If the cron expression is invalid, the tool MUST reject the request and return an error. No row SHALL be inserted.

The tool SHALL compute `next_run_at` using `croniter.get_next()` from the current time and persist it on the new row.

The `name` column has a UNIQUE constraint. If a task with the given name already exists, the tool MUST reject the request and return an error.

The tool SHALL return the `id` (UUID) of the newly created task.

#### Scenario: Successful task creation

WHEN `schedule_create` is called with `name='nightly-backup'`, `cron='0 2 * * *'`, and `prompt='Run backup procedure'`
THEN a new row SHALL be inserted into `scheduled_tasks`
AND the row SHALL have `source='db'`, `enabled=true`, and a non-null `next_run_at`
AND the tool SHALL return the UUID of the new row.

#### Scenario: Invalid cron expression rejected

WHEN `schedule_create` is called with `cron='not-a-cron'`
THEN the tool SHALL return an error indicating the cron expression is invalid
AND no row SHALL be inserted into `scheduled_tasks`.

#### Scenario: Duplicate name rejected

WHEN `schedule_create` is called with `name='daily-review'`
AND a task with `name='daily-review'` already exists in the database
THEN the tool SHALL return an error indicating the name is already in use
AND no row SHALL be inserted.

### Requirement: Task listing via MCP tool

The `schedule_list` MCP tool SHALL return all rows from the `scheduled_tasks` table. The result MUST include all columns: `id`, `name`, `cron`, `prompt`, `source`, `enabled`, `last_run_at`, `next_run_at`, `last_result`, `created_at`, and `updated_at`.

#### Scenario: List all scheduled tasks

WHEN `schedule_list` is called
AND the database contains three tasks (two enabled, one disabled)
THEN the tool SHALL return all three tasks with their complete column data.

#### Scenario: List with no tasks

WHEN `schedule_list` is called
AND the `scheduled_tasks` table is empty
THEN the tool SHALL return an empty list.

### Requirement: Task update via MCP tool

The `schedule_update` MCP tool SHALL accept a task `id` (UUID) and optional fields: `cron`, `prompt`, and `enabled`. At least one optional field MUST be provided.

If `cron` is provided, the tool SHALL validate it using `croniter.is_valid()` (or equivalent). If invalid, the tool MUST reject the request and return an error. No update SHALL be applied.

If the `id` does not match any existing task, the tool MUST return a not-found error.

When `cron` is updated or `enabled` is changed to `true`, the tool SHALL recompute `next_run_at` using `croniter.get_next()` from the current time.

When `enabled` is changed to `false`, the tool SHALL set `next_run_at` to NULL.

The tool SHALL update `updated_at` to the current time on every successful update.

#### Scenario: Update cron expression

WHEN `schedule_update` is called with a valid `id` and `cron='30 6 * * *'`
THEN the row SHALL have the new cron expression
AND `next_run_at` SHALL be recomputed from the new expression
AND `updated_at` SHALL be set to the current time.

#### Scenario: Disable a task

WHEN `schedule_update` is called with a valid `id` and `enabled=false`
THEN the row SHALL have `enabled=false`
AND `next_run_at` SHALL be set to NULL
AND `updated_at` SHALL be set to the current time.

#### Scenario: Re-enable a task

WHEN `schedule_update` is called with a valid `id` and `enabled=true`
THEN the row SHALL have `enabled=true`
AND `next_run_at` SHALL be recomputed using the task's cron expression from the current time
AND `updated_at` SHALL be set to the current time.

#### Scenario: Invalid cron on update rejected

WHEN `schedule_update` is called with `cron='bad'`
THEN the tool SHALL return an error
AND no changes SHALL be applied to the row.

#### Scenario: Update non-existent task

WHEN `schedule_update` is called with an `id` that does not exist in the database
THEN the tool SHALL return a not-found error.

### Requirement: Task deletion via MCP tool

The `schedule_delete` MCP tool SHALL accept a task `id` (UUID) and delete the corresponding row from `scheduled_tasks`.

Only tasks with `source='db'` SHALL be eligible for deletion. If the task has `source='toml'`, the tool MUST reject the request and return an error instructing the caller to disable the task instead.

If the `id` does not match any existing task, the tool MUST return a not-found error.

#### Scenario: Delete a DB-source task

WHEN `schedule_delete` is called with the `id` of a task that has `source='db'`
THEN the row SHALL be removed from the `scheduled_tasks` table.

#### Scenario: Attempt to delete a TOML-source task

WHEN `schedule_delete` is called with the `id` of a task that has `source='toml'`
THEN the tool SHALL return an error indicating that TOML-source tasks cannot be deleted and can only be disabled
AND the row SHALL remain in the database.

#### Scenario: Delete non-existent task

WHEN `schedule_delete` is called with an `id` that does not exist in the database
THEN the tool SHALL return a not-found error.

### Requirement: tick() dispatches due tasks to LLM CLI spawner

The `tick()` handler SHALL query the `scheduled_tasks` table for all tasks where `enabled=true` AND `next_run_at <= now()`. These are "due tasks."

For each due task, the scheduler SHALL dispatch the task's `prompt` to the LLM CLI spawner. Due tasks SHALL be processed serially -- the scheduler MUST NOT dispatch multiple prompts concurrently within a single tick.

After each dispatch completes (success or failure), the scheduler SHALL update the task row:
- `last_run_at` SHALL be set to the current time.
- `next_run_at` SHALL be recomputed using `croniter.get_next()` from the current time.
- `last_result` SHALL be set to a JSONB object containing the outcome. On success, this MUST include the runtime session result. On failure, this MUST include the error message.
- `updated_at` SHALL be set to the current time.

If no tasks are due, `tick()` SHALL return without dispatching anything.

#### Scenario: Single due task dispatched

WHEN `tick()` is called
AND one task has `enabled=true` and `next_run_at` in the past
THEN the scheduler SHALL dispatch that task's prompt to the LLM CLI spawner
AND `last_run_at` SHALL be set to approximately now
AND `next_run_at` SHALL be advanced to the next occurrence per the cron expression
AND `last_result` SHALL contain the runtime session outcome.

#### Scenario: Multiple due tasks processed serially

WHEN `tick()` is called
AND three tasks are due (next_run_at <= now, enabled=true)
THEN the scheduler SHALL dispatch each task's prompt to the LLM CLI spawner one at a time
AND each task's `last_run_at`, `next_run_at`, and `last_result` SHALL be updated after its dispatch completes
AND the second task SHALL NOT begin dispatching until the first task's dispatch has completed.

#### Scenario: No due tasks

WHEN `tick()` is called
AND no tasks have `enabled=true` AND `next_run_at <= now()`
THEN the scheduler SHALL not dispatch any prompts to the LLM CLI spawner.

#### Scenario: Disabled tasks are skipped

WHEN `tick()` is called
AND a task has `enabled=false` and `next_run_at` in the past
THEN that task SHALL NOT be dispatched.

#### Scenario: LLM CLI spawner returns an error

WHEN `tick()` is called
AND a due task's prompt dispatch to the LLM CLI spawner fails with an error
THEN `last_result` SHALL contain the error information
AND `last_run_at` SHALL still be updated
AND `next_run_at` SHALL still be advanced to the next cron occurrence
AND processing SHALL continue to the next due task (the error MUST NOT halt the tick).

### Requirement: next_run_at computation uses croniter

All `next_run_at` computations SHALL use `croniter.get_next()` with the current time as the base. The resulting value SHALL be a timezone-aware UTC timestamp.

The `cron` field SHALL support standard five-field cron syntax (minute, hour, day-of-month, month, day-of-week).

#### Scenario: Compute next run for daily task

WHEN a task has `cron='0 9 * * *'`
AND the current time is 2026-02-09T10:00:00Z
THEN `next_run_at` SHALL be computed as 2026-02-10T09:00:00Z.

#### Scenario: Compute next run for task due soon

WHEN a task has `cron='*/15 * * * *'`
AND the current time is 2026-02-09T10:03:00Z
THEN `next_run_at` SHALL be computed as 2026-02-09T10:15:00Z.
