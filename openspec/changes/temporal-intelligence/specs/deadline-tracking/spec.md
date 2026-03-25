## ADDED Requirements

### Requirement: Deadline Registration
Butlers SHALL register deadlines via a `deadline_create` MCP tool. A deadline is a scheduled_task with `task_type='deadline'` and additional metadata: `target_date` (date, required), `lead_time_days` (integer, required -- how many days before target_date to begin alerting), `alert_thresholds` (JSONB array of `{days_before: int, severity: string}`, required -- at least one threshold), and `deadline_status` (enum: `pending`, `alerted`, `escalated`, `completed`, `expired`).

#### Scenario: Create a simple deadline
- **WHEN** `deadline_create(name="visa-renewal", target_date="2026-08-15", lead_time_days=42, alert_thresholds=[{days_before: 42, severity: "info"}, {days_before: 14, severity: "warning"}, {days_before: 3, severity: "critical"}], prompt="Begin visa renewal process")` is called
- **THEN** a `scheduled_tasks` row is inserted with `task_type='deadline'`, `dispatch_mode='prompt'`, `target_date='2026-08-15'`, `lead_time_days=42`, `alert_thresholds` as provided, `deadline_status='pending'`, and `enabled=true`
- **AND** the task's UUID is returned

#### Scenario: Deadline requires at least one alert threshold
- **WHEN** `deadline_create(name="empty-threshold", target_date="2026-12-01", lead_time_days=30, alert_thresholds=[])` is called
- **THEN** a `ValueError` is raised requiring at least one alert threshold

#### Scenario: Deadline target_date must be in the future
- **WHEN** `deadline_create(name="past-deadline", target_date="2020-01-01", lead_time_days=7, alert_thresholds=[{days_before: 7, severity: "info"}])` is called
- **THEN** a `ValueError` is raised indicating the target date must be in the future

#### Scenario: Alert threshold days_before must not exceed lead_time_days
- **WHEN** `deadline_create(name="bad-threshold", target_date="2026-12-01", lead_time_days=14, alert_thresholds=[{days_before: 30, severity: "info"}])` is called
- **THEN** a `ValueError` is raised indicating threshold days_before cannot exceed lead_time_days

### Requirement: Deadline Countdown Evaluation
The scheduler's `tick()` function SHALL evaluate deadline-type tasks by computing `days_remaining = (target_date - now().date()).days` for each enabled deadline. When `days_remaining` matches or falls below a threshold's `days_before` value and that threshold has not yet fired, the task SHALL be dispatched with threshold metadata injected into the prompt or job context.

#### Scenario: Deadline threshold fires on matching day
- **WHEN** `tick()` runs and a deadline has `target_date='2026-08-15'` with threshold `{days_before: 42, severity: "info"}`
- **AND** today is 2026-07-04 (42 days before target)
- **THEN** the deadline task is dispatched with `threshold={days_before: 42, severity: "info"}` in the dispatch context
- **AND** `deadline_status` transitions from `pending` to `alerted`

#### Scenario: Deadline threshold fires when past threshold day
- **WHEN** `tick()` runs and a deadline has threshold `{days_before: 14, severity: "warning"}`
- **AND** `days_remaining` is 12 (threshold was missed due to downtime)
- **AND** this threshold has not yet fired
- **THEN** the deadline task is dispatched with the threshold metadata
- **AND** the threshold is marked as fired

#### Scenario: Already-fired threshold does not re-fire
- **WHEN** `tick()` runs and a deadline threshold has already been fired
- **AND** `days_remaining` still satisfies that threshold
- **THEN** the threshold is NOT dispatched again

#### Scenario: Deadline expires after target date
- **WHEN** `tick()` runs and a deadline's `target_date` has passed
- **AND** `deadline_status` is not `completed`
- **THEN** `deadline_status` transitions to `expired`
- **AND** the task is set to `enabled=false`

### Requirement: Deadline Status Transitions
Deadline status SHALL follow a defined state machine: `pending` -> `alerted` (first threshold fires) -> `escalated` (a `critical` severity threshold fires) -> `completed` (manually marked) or `expired` (target date passes). Status can also transition directly from `pending` to `expired` if no thresholds fire before the target date.

#### Scenario: Status transitions from pending to alerted
- **WHEN** the first non-critical threshold fires on a `pending` deadline
- **THEN** `deadline_status` transitions to `alerted`

#### Scenario: Status transitions from alerted to escalated
- **WHEN** a threshold with `severity="critical"` fires on an `alerted` deadline
- **THEN** `deadline_status` transitions to `escalated`

#### Scenario: Deadline marked completed
- **WHEN** `deadline_update(id, deadline_status="completed")` is called
- **THEN** `deadline_status` transitions to `completed`
- **AND** the task is set to `enabled=false`
- **AND** no further thresholds fire

### Requirement: Deadline CRUD Tools
The module SHALL register MCP tools: `deadline_create`, `deadline_update`, `deadline_list`, `deadline_delete`. These wrap the existing `schedule_*` CRUD with deadline-specific validation.

#### Scenario: List deadlines with status filter
- **WHEN** `deadline_list(status="pending")` is called
- **THEN** only `scheduled_tasks` rows with `task_type='deadline'` and `deadline_status='pending'` are returned

#### Scenario: Update deadline target date
- **WHEN** `deadline_update(id, target_date="2026-09-01")` is called
- **THEN** the `target_date` is updated
- **AND** all threshold fired-flags are reset (thresholds re-evaluate against new date)
- **AND** `deadline_status` resets to `pending`

#### Scenario: Delete deadline
- **WHEN** `deadline_delete(id)` is called for a `task_type='deadline'` task
- **THEN** the row is removed (same as `schedule_delete` for `source='db'` tasks)

#### Scenario: Cannot delete TOML-sourced deadline
- **WHEN** `deadline_delete(id)` is called for a deadline with `source='toml'`
- **THEN** a `ValueError` is raised

### Requirement: Deadline Dependencies
Deadlines MAY declare dependencies on other deadlines via a `depends_on` field (array of deadline task UUIDs). A deadline with unresolved dependencies SHALL NOT fire its thresholds until all dependencies are marked `completed`.

#### Scenario: Dependent deadline blocked
- **WHEN** deadline B has `depends_on=[deadline_A_id]`
- **AND** deadline A has `deadline_status='pending'`
- **AND** deadline B's first threshold day arrives
- **THEN** deadline B's threshold is NOT dispatched

#### Scenario: Dependent deadline unblocked
- **WHEN** deadline A transitions to `deadline_status='completed'`
- **AND** deadline B has `depends_on=[deadline_A_id]`
- **THEN** deadline B's thresholds begin evaluating normally on the next tick

### Requirement: Deadline Prompt Context Injection
When a deadline task is dispatched in `prompt` mode, the prompt SHALL be augmented with structured context including `target_date`, `days_remaining`, `fired_threshold` (the threshold that triggered this dispatch), `deadline_status`, and `all_thresholds` (for the LLM to understand the full alert timeline).

#### Scenario: Prompt includes deadline context
- **WHEN** a deadline with `prompt="Begin visa renewal process"` fires its 42-day threshold
- **THEN** the dispatched prompt includes the original prompt text plus structured deadline context: `target_date`, `days_remaining=42`, `fired_threshold={days_before: 42, severity: "info"}`, and the full list of thresholds
