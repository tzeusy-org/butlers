# Scheduler: Core Infrastructure Contract

Status: Normative (Target State)
Last updated: 2026-02-24
Primary owner: Platform/Core

## 1. Overview

The Scheduler is a core butler infrastructure component responsible for time-driven task dispatch. It evaluates cron expressions at configurable intervals, identifies due tasks, and triggers them through the spawner to invoke the LLM CLI with task prompts or structured jobs.

**Key responsibilities:**
- Maintain a registry of scheduled tasks (TOML-sourced and runtime-created).
- Evaluate cron expressions to determine dispatch timing.
- Serialize task dispatch to avoid concurrent spawns of the same butler.
- Persist task execution metadata (last run, result, next scheduled time).
- Support projection fields for calendar integration and visibility.
- Auto-disable tasks when `until_at` boundaries pass.
- Implement deterministic staggering to avoid thundering herd on frequent cadences.

This document defines the contract for scheduler behavior, data model, dispatch semantics, and integration with calendar modules.

## 2. Design Goals

- **Cron-first dispatch:** standard 5-field cron expressions drive all scheduling logic.
- **TOML-as-code:** butler config defines core schedules; runtime API enables dynamic task creation.
- **Deterministic staggering:** tasks with the same cadence are dispersed by a deterministic hash to avoid synchronized surges.
- **Fail-resilient:** a failed dispatch does not skip future occurrences; the task's cron advances independently.
- **Transparent projection:** schedule metadata can be exposed to calendar modules for unified butler event surfaces.
- **Flexible dispatch modes:** support both text prompts (for LLM CLI) and structured job names with arguments.
- **Optional calendar linkage:** scheduled tasks can carry calendar event IDs for bidirectional tracking and editing.
- **Audit-first:** all dispatch attempts and outcomes are persisted for observability and replay.

## 3. Applicability and Boundaries

### In scope
- Cron expression validation and evaluation via `croniter`.
- TOML-to-DB synchronization of butler config schedules.
- Runtime task CRUD (create, read, update, delete).
- Task dispatch via the spawner (prompt and job modes).
- Dispatch result persistence and task state advancement.
- Staggering algorithm to reduce peak load on synchronized cadences.
- Task metadata for calendar projection (timezone, start_at, end_at, until_at, display_title).
- Auto-disabling of tasks when `until_at` boundaries expire.
- Telemetry and tracing (via OpenTelemetry).

### Out of scope
- Real-time task execution (all scheduling is evaluated periodically by the daemon tick loop).
- User-facing scheduler UI/dashboard (the calendar workspace displays projected scheduler entries).
- Timezone arithmetic for cron evaluation (cron expressions are always evaluated in UTC; timezone fields are for projection/display only).
- Job queueing or background worker pools (the spawner handles ephemeral invocation).
- Recurring task series splitting or instance-level modification (recurrence is strictly cron-based; use the calendar workspace for RRULE-based events with fine-grained editing).

## 4. Data Model Contract

### 4.1 Table: `scheduled_tasks`

The canonical table for all scheduled tasks, with both TOML-sourced (read-only, managed by daemon sync) and DB-sourced (runtime-created, mutable) rows.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | UUID | PRIMARY KEY, DEFAULT `gen_random_uuid()` | Unique stable identifier. |
| `name` | TEXT | UNIQUE, NOT NULL | Human-readable task name. Used as TOML lookup key and dispatch identifier. |
| `cron` | TEXT | NOT NULL | 5-field cron expression (minute hour day month day-of-week). Must pass `croniter.is_valid()`. |
| `dispatch_mode` | TEXT | NOT NULL, DEFAULT 'prompt', CHECK IN ('prompt', 'job') | Dispatch style: 'prompt' sends a text prompt to the LLM CLI spawner; 'job' sends a structured job name + args. |
| `prompt` | TEXT | NULL, constraint: set IFF dispatch_mode='prompt' | Prompt text sent to LLM CLI when dispatch_mode is 'prompt'. Required and non-empty for prompt mode. |
| `job_name` | TEXT | NULL, constraint: set IFF dispatch_mode='job' | Job identifier sent to spawner when dispatch_mode is 'job'. Required and non-empty for job mode. |
| `job_args` | JSONB | NULL, constraint: valid object or null | Structured arguments passed to the job. Must be a JSON object (not array/null/scalar). |
| `timezone` | TEXT | NOT NULL, DEFAULT 'UTC' | IANA timezone identifier for projection/display (e.g., 'America/New_York'). Does NOT affect cron evaluation (always UTC). |
| `start_at` | TIMESTAMPTZ | NULL, constraint: must be timezone-aware if set | Calendar projection: earliest datetime when this task is active. Used for visibility windows and filtering. |
| `end_at` | TIMESTAMPTZ | NULL, constraint: must be timezone-aware if set; end_at > start_at if both set | Calendar projection: latest datetime when this task is active. Exclusive boundary for range queries. |
| `until_at` | TIMESTAMPTZ | NULL, constraint: must be timezone-aware if set; until_at >= start_at if both set | Calendar projection: task auto-disables after this datetime. Used for bounded recurring task series. |
| `display_title` | TEXT | NULL | Calendar projection: human-friendly display title for dashboard surfaces. Defaults to `name` if not set. |
| `calendar_event_id` | UUID | NULL, UNIQUE (partial index WHERE calendar_event_id IS NOT NULL) | Bidirectional link to calendar module (for event edits that sync back to scheduler via `calendar_update_butler_event`). |
| `source` | TEXT | NOT NULL, DEFAULT 'db', CHECK IN ('toml', 'db') | Origin: 'toml' = synced from butler config (read-only at DB level); 'db' = created via runtime API (mutable). |
| `enabled` | BOOLEAN | NOT NULL, DEFAULT true | Dispatch gate. When false, task is skipped even if due. |
| `next_run_at` | TIMESTAMPTZ | NULL | Computed: next cron occurrence in UTC. NULL when disabled. |
| `last_run_at` | TIMESTAMPTZ | NULL | Audit: when the task last executed (regardless of success/failure). |
| `last_result` | JSONB | NULL | Audit: result object (on success) or `{"error": "message"}` (on failure) from last dispatch. |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | Timestamp when row was inserted. |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | Timestamp when row was last modified. |

### 4.2 Constraints and Validation

#### Dispatch payload constraints
```sql
CONSTRAINT scheduled_tasks_dispatch_payload_check
    CHECK (
        (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
        OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
    )
```
Enforces mutual exclusion: prompt mode requires `prompt` and forbids `job_name`; job mode requires `job_name` and forbids `prompt`.

#### Window bounds constraints
```sql
CONSTRAINT scheduled_tasks_window_bounds_check
    CHECK (start_at IS NULL OR end_at IS NULL OR end_at > start_at)
CONSTRAINT scheduled_tasks_until_bounds_check
    CHECK (until_at IS NULL OR start_at IS NULL OR until_at >= start_at)
```
Ensures projection fields are internally consistent: `end_at > start_at` and `until_at >= start_at`.

#### Calendar event uniqueness
```sql
CREATE UNIQUE INDEX ix_scheduled_tasks_calendar_event_id
ON scheduled_tasks (calendar_event_id)
WHERE calendar_event_id IS NOT NULL
```
At most one scheduled task can reference a given `calendar_event_id`, enabling bidirectional calendar-to-scheduler edits.

## 5. Cron Semantics and Evaluation

### 5.1 Cron expression format
- 5-field format: `minute hour day month day-of-week`
- Field ranges: minute (0–59), hour (0–23), day (1–31), month (1–12), day-of-week (0–6, 0=Sunday).
- Common patterns:
  - `0 9 * * *` → 9:00 AM UTC every day.
  - `0 */4 * * *` → every 4 hours (0:00, 4:00, 8:00, 12:00, 16:00, 20:00 UTC).
  - `0 0 * * 1` → midnight UTC every Monday.
  - `30 14 15 * *` → 2:30 PM UTC on the 15th of every month.
- Validation: all cron expressions are validated using `croniter.is_valid()` at creation/update time. Invalid expressions are rejected with a `ValueError`.

### 5.2 Evaluation and next_run_at computation
- `next_run_at` is the next UTC datetime when the cron expression will trigger.
- Computed at task creation, update, and after each dispatch via `_next_run(cron, stagger_key, max_stagger_seconds, now)`.
- Timezone parameter does not affect cron evaluation; it is purely for projection/display (calendar module interprets events in the task's configured timezone).

### 5.3 Dispatch timing
- The daemon's tick handler queries all rows WHERE `enabled=true AND next_run_at <= now()` in UTC.
- Due tasks are dispatched in order of `next_run_at` (oldest first).
- After dispatch (success or failure), `next_run_at` is advanced to the next cron occurrence via `_next_run(...)`.
- If the next occurrence is in the past (e.g., system clock resets or heavy load), the task is considered due again on the next tick and will be re-dispatched.

## 6. Staggering

### 6.1 Motivation
When many tasks share the same cron cadence (e.g., 100 hourly tasks), naive evaluation causes a synchronized surge of spawns. Staggering introduces a deterministic, stable offset to distribute task executions across the cron interval.

### 6.2 Staggering algorithm
For a given task with `stagger_key` (typically the task name or butler ID) and max offset limit:
1. Compute the interval between the next two cron occurrences (the cadence) in seconds.
2. Compute max safe offset = min(`max_stagger_seconds`, cadence - 1).
3. Hash the `stagger_key` using SHA-256 to obtain a deterministic byte sequence.
4. Map the hash to an offset: `offset = (hash_as_int % (max_safe_offset + 1))` seconds.
5. Add the offset to the next cron occurrence.

### 6.3 Offset boundaries
- Default max stagger: 15 minutes (900 seconds).
- Offset never exceeds the task's cron cadence minus 1 second (to avoid skipping occurrences).
- Same `stagger_key` always produces the same offset, even across daemon restarts.
- Different keys with identical cron expressions receive different (deterministically random) offsets.

### 6.4 Configuration
Staggering is controlled by parameters passed to `sync_schedules()`, `schedule_create()`, `schedule_update()`, and `tick()`:
- `stagger_key` (str | None): if None or empty, no staggering is applied.
- `max_stagger_seconds` (int, default 900): maximum offset in seconds. Typically set once at daemon startup per butler.

## 7. Dispatch Modes

### 7.1 Prompt mode (dispatch_mode='prompt')
- **When:** task has `dispatch_mode='prompt'` and a non-empty `prompt` text field.
- **Invocation:** spawner is called with `dispatch_fn(prompt=prompt, trigger_source=f"schedule:{name}")`.
- **Behavior:** LLM CLI is spawned with the prompt text and can use all tools available to the butler.
- **Use case:** open-ended butler actions (e.g., "summarize recent emails", "check calendar conflicts").

### 7.2 Job mode (dispatch_mode='job')
- **When:** task has `dispatch_mode='job'`, a non-empty `job_name` field, and optional `job_args` object.
- **Invocation:** spawner is called with `dispatch_fn(job_name=job_name, job_args=job_args, trigger_source=f"schedule:{name}")`.
- **Behavior:** spawner invokes a specific job handler (typically a module-registered skill or job processor) with structured arguments.
- **Use case:** well-defined, repeatable actions (e.g., "sync_calendar_changes", "fetch_mail", job args like `{"folder": "inbox", "limit": 100}`).

### 7.3 Trigger source
All dispatches carry a `trigger_source` field set to `f"schedule:{name}"` where `name` is the task's name. This allows the butler to distinguish scheduled invocations from user-initiated triggers or webhook-driven events.

## 8. Task Lifecycle and Enables/Disables

### 8.1 Task creation
Scenarios:
- **TOML-sourced:** `sync_schedules()` reads butler config at daemon startup and inserts/updates rows with `source='toml'`.
- **Runtime API:** `schedule_create()` inserts a new row with `source='db'` and user-provided fields.

Initial state after creation:
- `enabled=true` (unless explicitly disabled at create time, not currently supported).
- `next_run_at` is computed from the cron expression and current UTC time.
- `last_run_at` and `last_result` are NULL.

### 8.2 Disabling via sync or update
- **TOML removal:** if a task is present in the DB with `source='toml'` but missing from butler config, `sync_schedules()` sets `enabled=false` and leaves the row in the DB (for audit/history).
- **Manual disable:** `schedule_update(..., enabled=False)` sets `enabled=false` and `next_run_at=NULL` (prevents accidental re-dispatch).
- **Re-enabling:** `schedule_update(..., enabled=True)` recalculates `next_run_at` from the current cron expression.

### 8.3 Auto-disable via until_at
- **Until boundary:** if a task has `until_at` set, the tick handler checks whether `now() > until_at` before dispatching.
- **Current behavior:** if the until boundary has passed, the task is skipped during this tick. Future ticks will also skip it.
- **Target state:** an automatic disable step should mark `enabled=false` when the boundary passes, for clearer UI indication and reduced query overhead.
- **Use case:** "remind me daily until March 1st" — the task continues to run but becomes inactive after the date.

### 8.4 Deletion
- **DB-sourced tasks:** `schedule_delete(pool, task_id)` removes the row entirely.
- **TOML-sourced tasks:** cannot be deleted via API. Remove from butler config and re-run `sync_schedules()` to mark disabled.

## 9. Synchronization: TOML to Database

### 9.1 Sync flow
At daemon startup (or on explicit trigger), `sync_schedules(pool, schedules)` reads butler config `[[butler.schedule]]` entries and reconciles them with the `scheduled_tasks` table:

1. Iterate over TOML schedule entries and normalize each (validate cron, dispatch mode, etc.).
2. Fetch all existing DB rows with `source='toml'`.
3. For each TOML entry:
   - If a matching row exists (by `name`): compare cron, prompt, dispatch mode, job_name, job_args. If any differ or the row was disabled, update the row and set `enabled=true`.
   - If no matching row exists: insert a new row with `source='toml'` and `enabled=true`.
4. For each DB row with `source='toml'` not present in TOML: set `enabled=false` (marking it disabled without deletion, preserving history).

### 9.2 Determinism
- Matching by `name` field ensures stable identity across config reloads.
- Updated rows retain their `id` and `created_at`, preserving history.
- `updated_at` and `next_run_at` are refreshed on changes.

## 10. Dispatch and Execution

### 10.1 The tick handler
The `tick(pool, dispatch_fn, ...)` function is the core dispatch loop called periodically by the daemon (typically every minute or on a custom interval):

1. Query all rows WHERE `enabled=true AND next_run_at <= now()` in UTC, ordered by `next_run_at`.
2. For each row:
   - Extract dispatch parameters (prompt/job_name/job_args/cron/dispatch_mode).
   - Invoke `dispatch_fn(prompt=... or job_name=..., trigger_source=f"schedule:{name}")`.
   - If dispatch succeeds: capture the result object and serialize it to JSON.
   - If dispatch fails: capture the exception and serialize as `{"error": "..."}`.
   - Increment the dispatched counter.
3. For each row (whether dispatch succeeded or failed):
   - Compute the next cron occurrence via `_next_run(...)`.
   - Update the row: set `next_run_at`, `last_run_at=now()`, `last_result=<JSON>`, `updated_at=now()`.
4. Emit telemetry: set span attributes `tasks_due` (count of due tasks queried) and `tasks_run` (count successfully dispatched).
5. Return the count of successfully dispatched tasks.

### 10.2 Serial dispatch
All due tasks are dispatched in a **serial** loop (one at a time) to avoid concurrent spawns of the same butler. If one dispatch is slow, subsequent tasks are delayed proportionally.

### 10.3 Failure handling
- A dispatch failure (exception raised by `dispatch_fn`) does NOT skip future occurrences of the task.
- The exception message is captured in `last_result` as `{"error": "..."}` for audit.
- The cron is advanced normally; the task will trigger again at its next scheduled time.
- Failures are logged at ERROR level with the task name and exception traceback.

### 10.4 Result storage
- `last_result` stores the result of the most recent dispatch (success or failure) as a JSONB object.
- On success: the spawner's result object (typically a `SpawnerResult` with metadata) is serialized to JSON.
- On failure: `{"error": "<exception string>"}` is stored.
- `last_result` can be queried for debugging and audit purposes but is not exposed in the public API contract (no guarantee of schema stability).

## 11. Projection Fields for Calendar Integration

### 11.1 Calendar projection fields
The scheduler table carries optional fields for calendar module integration, allowing scheduled tasks to be rendered and edited as calendar events in the `/butlers/calendar` workspace:

- `timezone` (str, default 'UTC'): IANA timezone identifier for display and event expansion.
- `start_at` (datetime | NULL): earliest datetime when the task is active. Used for visibility windows.
- `end_at` (datetime | NULL): latest datetime when the task is active. Used for range filtering.
- `until_at` (datetime | NULL): task auto-disables after this datetime. Used for bounded series endpoints.
- `display_title` (str | NULL): friendly name for calendar surfaces (defaults to `name` if not set).
- `calendar_event_id` (UUID | NULL): bidirectional link to a calendar event row.

### 11.2 Semantics
- `start_at` and `end_at` define a visibility window. The task is considered "active" only within this interval.
- A task with `start_at=2026-03-01T09:00:00+00:00` and `until_at=2026-04-01T00:00:00+00:00` will:
  - First dispatch on or after 2026-03-01.
  - Stop triggering after 2026-04-01.
- `display_title` is optional; if not provided, the UI renders the `name` field.
- `calendar_event_id` is a one-to-one link: if set, the scheduler row is bound to a calendar event for UI editing.

### 11.3 Calendar projection
The calendar module reads scheduled tasks and projects them into a unified calendar view via a `UnifiedCalendarEntry` mapping:
- `source_type` = "scheduled_task"
- `title` = `display_title` or `name`
- `start_at` = from cron expansion (e.g., next few occurrences) or `start_at` field
- `timezone` = from `timezone` field
- `rrule` = (not applicable; cron is not directly translatable to RRULE)
- `cron` = the task's cron expression (for UI re-editing)
- `until_at` = from `until_at` field
- `schedule_id` = the task's UUID (for mutations)
- `butler_name` = the butler that owns this scheduler

## 12. Timezone Handling

### 12.1 Cron evaluation timezone
- All cron expressions are evaluated in **UTC only**. The `timezone` field does NOT affect when the cron triggers.
- Example: a task with `cron="0 9 * * *"` (9:00 AM) and `timezone="America/New_York"` will:
  - Trigger at 09:00:00 UTC (which may be 4:00 AM or 5:00 AM in New York depending on DST).
  - Be displayed to the user as "9:00 AM UTC" or projected in New York time by the calendar module.

### 12.2 Timezone field usage
The `timezone` field is **informational** for projection and display:
- Calendar module uses it to render task occurrences in the butler's local timezone.
- When a task is projected as a calendar entry, the calendar module converts all times from UTC to the task's `timezone` for consistent display.
- All stored datetimes (`next_run_at`, `start_at`, `end_at`, `until_at`, `last_run_at`) are timezone-aware PostgreSQL `TIMESTAMPTZ` values in UTC internally.

### 12.3 Default timezone
New tasks default to `timezone='UTC'` if not explicitly set. Callers should set the timezone to match the butler's configured timezone or the user's local timezone for correct projection.

## 13. Database Interactions: CRUD API

### 13.1 schedule_list
```python
async def schedule_list(pool: asyncpg.Pool) -> list[dict[str, Any]]
```
Returns all scheduled tasks ordered by `name`.

**Returns:** list of task dicts with all fields (including `job_args` decoded from JSONB to dict).

**Raises:** ValueError if a task's `job_args` JSONB is invalid JSON.

### 13.2 schedule_create
```python
async def schedule_create(
    pool: asyncpg.Pool,
    name: str,
    cron: str,
    prompt: str | None = None,
    *,
    dispatch_mode: str = 'prompt',
    job_name: str | None = None,
    job_args: dict[str, Any] | None = None,
    timezone: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    until_at: datetime | None = None,
    display_title: str | None = None,
    calendar_event_id: uuid.UUID | str | None = None,
    stagger_key: str | None = None,
    max_stagger_seconds: int = 900,
) -> uuid.UUID
```
Creates a new runtime scheduled task.

**Parameters:**
- `name` (str): unique task identifier, human-readable.
- `cron` (str): 5-field cron expression; must pass `croniter.is_valid()`.
- `prompt` (str | None): for prompt mode, the text sent to LLM CLI.
- `dispatch_mode` (str): 'prompt' or 'job'.
- `job_name` (str | None): for job mode, the job identifier.
- `job_args` (dict | None): for job mode, structured arguments.
- `timezone` (str | None): IANA timezone for projection (defaults to 'UTC').
- `start_at`, `end_at`, `until_at` (datetime | None): projection fields (must be timezone-aware if set).
- `display_title` (str | None): friendly name for UI.
- `calendar_event_id` (UUID | str | None): optional calendar linkage.
- `stagger_key`, `max_stagger_seconds`: staggering config.

**Returns:** UUID of the newly created task.

**Raises:**
- `ValueError` if `name` already exists (UNIQUE constraint).
- `ValueError` if cron is invalid.
- `ValueError` if dispatch mode is invalid or constraints are violated.
- `ValueError` if projection fields fail validation (non-aware datetimes, bounds violations, etc.).

### 13.3 schedule_update
```python
async def schedule_update(
    pool: asyncpg.Pool,
    task_id: uuid.UUID,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = 900,
    **fields,
) -> None
```
Updates fields on an existing scheduled task. Only allowed fields are accepted; others raise `ValueError`.

**Allowed fields:**
- `name`, `cron`, `dispatch_mode`, `prompt`, `job_name`, `job_args`
- `enabled`
- `timezone`, `start_at`, `end_at`, `until_at`, `display_title`, `calendar_event_id`

**Behavior:**
- If `cron` is updated: `next_run_at` is recalculated from the new cron expression.
- If `enabled` is set to True: `next_run_at` is recalculated.
- If `enabled` is set to False: `next_run_at` is set to NULL.
- Dispatch-related fields (`dispatch_mode`, `prompt`, `job_name`, `job_args`) are validated together to ensure mode-specific constraints (e.g., prompt mode forbids `job_name`).

**Raises:**
- `ValueError` if `task_id` is not found.
- `ValueError` if an invalid field is specified.
- `ValueError` if cron is invalid.
- `ValueError` if projection field constraints are violated.

### 13.4 schedule_delete
```python
async def schedule_delete(pool: asyncpg.Pool, task_id: uuid.UUID) -> None
```
Deletes a runtime scheduled task.

**Restrictions:** Cannot delete TOML-sourced tasks (with `source='toml'`). Must remove from butler config and run sync instead.

**Raises:**
- `ValueError` if `task_id` is not found.
- `ValueError` if the task has `source='toml'`.

## 14. Validation and Error Handling

### 14.1 Validation helpers
- `_normalize_schedule_dispatch(...)`: validates dispatch_mode, prompt, job_name, job_args and enforces mode-specific constraints. Raises `ValueError` on constraint violation.
- `_normalize_schedule_projection_fields(...)`: validates timezone, start_at, end_at, until_at, display_title, calendar_event_id. Raises `ValueError` if non-aware datetimes, invalid bounds, empty strings, etc.
- `_normalize_dispatch_mode(...)`: validates that dispatch_mode is a string and is one of the allowed values.

### 14.2 Common error scenarios
| Scenario | Error | Resolution |
|----------|-------|-----------|
| Invalid cron expression | `ValueError("Invalid cron expression: ...")` | Check cron syntax with `croniter.is_valid()`. |
| Prompt mode without prompt | `ValueError("... requires non-empty prompt")` | Provide `prompt` text for prompt mode. |
| Job mode without job_name | `ValueError("... requires non-empty job_name")` | Provide `job_name` for job mode. |
| Task name already exists | `ValueError("Task name '...' already exists")` | Use a unique name. |
| Task not found on update | `ValueError("Task <uuid> not found")` | Verify task UUID. |
| Attempt to delete TOML task | `ValueError("Cannot delete TOML-sourced task")` | Remove from butler config; sync will mark disabled. |
| Non-aware datetime in start_at | `ValueError("...start_at must be timezone-aware")` | Pass a `datetime` with `tzinfo` set (e.g., UTC or a zoneinfo IANA tz). |
| end_at <= start_at | `ValueError("...end_at must be after start_at")` | Ensure end_at > start_at. |

## 15. Observability and Telemetry

### 15.1 Logging
The scheduler logs at the following levels:
- **INFO:** on task creation, update, sync, and dispatch (`"Dispatched scheduled task: <name>"`, `"Updated TOML schedule: <name>"`).
- **ERROR:** on dispatch failures and validation errors.
- **DEBUG** (not shown by default): detailed sync reconciliation steps.

### 15.2 OpenTelemetry tracing
The `tick()` function creates a span `butler.tick` with attributes:
- `tasks_due` (int): count of due tasks found in this tick.
- `tasks_run` (int): count of tasks successfully dispatched.

Span is created via `tracer.start_as_current_span("butler.tick")`.

### 15.3 Metrics (future)
Recommended metrics for monitoring:
- Histogram of dispatch duration per task.
- Counter of dispatch success/failure per task.
- Gauge of pending due tasks.
- Counter of stagger offset distribution.

## 16. Integration Points

### 16.1 Spawner integration
The scheduler calls the spawner's `trigger()` function (async callable) with one of:
- `dispatch_fn(prompt=prompt, trigger_source=f"schedule:{name}")`
- `dispatch_fn(job_name=job_name, job_args=job_args, trigger_source=f"schedule:{name}")`

Spawner must handle both signatures and return a result object (typically a `SpawnerResult`).

### 16.2 Calendar module integration
The calendar module reads `scheduled_tasks` and projects them into `/butlers/calendar`:
- Queries tasks with `enabled=true` and within projection windows.
- Maps `cron`, `timezone`, `start_at`, `end_at`, `until_at`, `display_title`, `name` to calendar entry fields.
- Supports bidirectional edits: user edits a calendar entry → scheduler is updated via `schedule_update()` and `calendar_event_id` linkage.

### 16.3 Daemon tick loop
The daemon's main loop calls `tick(pool, dispatch_fn, ...)` at regular intervals (typically 1 minute). The interval is independent of task cadences; due tasks are identified by `next_run_at <= now()` queries.

## 17. Examples

### 17.1 TOML-sourced schedule
Butler config (butler.toml):
```toml
[[butler.schedule]]
name = "daily_digest"
cron = "0 9 * * *"
dispatch_mode = "prompt"
prompt = "Summarize emails from the last 24 hours and highlight any urgent messages"
```

At daemon startup: `sync_schedules()` creates a row:
- `id` = (new UUID)
- `name` = "daily_digest"
- `cron` = "0 9 * * *"
- `dispatch_mode` = "prompt"
- `prompt` = "Summarize emails..."
- `source` = "toml"
- `enabled` = true
- `next_run_at` = (computed from now)

At the scheduled time: `tick()` finds the row due and calls `dispatch_fn(prompt="Summarize emails...", trigger_source="schedule:daily_digest")`.

### 17.2 Runtime-created task with projection fields
Caller creates a task via API:
```python
task_id = await schedule_create(
    pool,
    name="daily_reminder",
    cron="0 9 * * *",
    prompt="Remind me to review my calendar for the day",
    timezone="America/New_York",
    start_at=datetime(2026, 3, 1, tzinfo=UTC),
    until_at=datetime(2026, 4, 1, tzinfo=UTC),
    display_title="Daily calendar review",
)
```

Result:
- Task will first trigger on or after 2026-03-01 09:00:00 UTC.
- Task will stop triggering after 2026-04-01 00:00:00 UTC.
- Calendar module projects it as a recurring daily event in New York timezone.
- UI shows "Daily calendar review" instead of "daily_reminder".

### 17.3 Job mode with structured arguments
Runtime API call:
```python
task_id = await schedule_create(
    pool,
    name="sync_gmail",
    cron="*/5 * * * *",  # every 5 minutes
    dispatch_mode="job",
    job_name="sync_inbox",
    job_args={"folder": "INBOX", "limit": 100, "mark_read": False},
)
```

At dispatch time: `dispatch_fn(job_name="sync_inbox", job_args={"folder": "INBOX", ...}, trigger_source="schedule:sync_gmail")`.

## 18. Non-Goals and Future Work

### Non-Goals
- Real-time scheduling (all scheduling is evaluated periodically by the tick loop).
- Arbitrary timezone-based cron evaluation (cron is always UTC; projection adapts for display).
- Multi-instance load balancing (a single daemon/butler instance owns its schedule; inter-butler coordination is via MCP).
- Recurrence rule (RRULE) evaluation (use calendar module for RRULE; scheduler is cron-only).

### Future work (target state)
- Auto-disable tasks when `until_at` passes (currently skipped by tick but not marked disabled).
- Structured job result schema and validation.
- Sub-minute cadence support (current granularity is 1 minute via tick loop).
- Scheduler dashboard page showing pending tasks, last run status, and next scheduled times.
- Bulk import/export of schedules (e.g., `schedule_bulk_create`, YAML/JSON formats).
- Retry policies for failed dispatches (e.g., exponential backoff, max retries).

## 19. Testing

### 19.1 Test categories
- **Unit tests** (`test_core_scheduler.py`): cron evaluation, staggering, validation, TOML sync reconciliation.
- **Integration tests**: database operations with real asyncpg pool, dispatch invocation, result storage.
- **E2E tests**: full daemon loop with scheduler enabled, calendar projection, state persistence.

### 19.2 Mocking
- Mock `dispatch_fn` to verify correct invocation signatures and trigger sources.
- Mock `croniter` to control cron evaluation (limited use; prefer real cron evaluation in tests).
- Use Docker postgres container for integration tests to ensure schema and constraint behavior.

### 19.3 Staggering verification
- Verify that same `stagger_key` produces same offset across multiple invocations.
- Verify that offset never exceeds cadence minus 1 second.
- Verify that different keys produce different offsets.

## 20. Backward Compatibility

The scheduler's data model is stable. Changes to the contract (e.g., new columns, constraints) require Alembic migrations and are versioned.

### Current version
- Baseline: `core_001` (full target-state baseline).
- Migrations: `core_002_add_dispatch_mode_columns`, `core_003_backfill_dispatch_mode`, `core_004_nullable_prompt`, `core_006_scheduler_calendar_linkage`.

Caller code using the scheduler API should:
- Validate cron expressions before calling `schedule_create()`.
- Provide timezone-aware datetimes for projection fields.
- Expect `last_result` JSONB to be unstructured (no schema guarantee).
- Treat `job_args` as opaque (validation is per-job, not by the scheduler).
