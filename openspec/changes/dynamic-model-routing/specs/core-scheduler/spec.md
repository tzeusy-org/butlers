## MODIFIED Requirements

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

### Requirement: TOML-to-DB Schedule Synchronization
At daemon startup, `sync_schedules()` reconciles `[[butler.schedule]]` TOML entries with the `scheduled_tasks` DB table. Matching is by `name` field. New entries are inserted with `source='toml'`, changed entries are updated, and TOML tasks removed from config are disabled (not deleted) to preserve history.

The `complexity` field is included in the sync comparison and persisted alongside other schedule fields.

#### Scenario: New TOML schedule inserted
- **WHEN** a TOML schedule entry has no matching row in DB
- **THEN** a new row is inserted with `source='toml'`, `enabled=true`, computed `next_run_at`, and `complexity` from TOML (default `medium`)

#### Scenario: Changed TOML schedule updated
- **WHEN** a TOML schedule entry's cron, prompt, dispatch_mode, job_name, job_args, or complexity differ from the DB row
- **THEN** the DB row is updated and `next_run_at` is recomputed

#### Scenario: Removed TOML schedule disabled
- **WHEN** a DB row with `source='toml'` has no matching TOML entry
- **THEN** the row is set to `enabled=false` (not deleted)

## ADDED Requirements

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
