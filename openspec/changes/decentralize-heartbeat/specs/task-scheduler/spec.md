# Task Scheduler

Delta spec for decentralize-heartbeat change.

## MODIFIED Requirements

### Requirement: tick() dispatches due tasks to LLM CLI spawner

The `tick()` handler SHALL query the `scheduled_tasks` table for all tasks where `enabled=true` AND `next_run_at <= now()`. These are "due tasks."

For each due task, the scheduler SHALL dispatch the task's `prompt` to the LLM CLI spawner. Due tasks SHALL be processed serially -- the scheduler MUST NOT dispatch multiple prompts concurrently within a single tick.

After each dispatch completes (success or failure), the scheduler SHALL update the task row:
- `last_run_at` SHALL be set to the current time.
- `next_run_at` SHALL be recomputed using `croniter.get_next()` from the current time.
- `last_result` SHALL be set to a JSONB object containing the outcome. On success, this MUST include the runtime session result. On failure, this MUST include the error message.
- `updated_at` SHALL be set to the current time.

If no tasks are due, `tick()` SHALL return without dispatching anything.

The `tick()` function is primarily invoked by the butler's internal scheduler loop (every 60 seconds by default). It is also exposed as an MCP tool for manual invocation and debugging.

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
