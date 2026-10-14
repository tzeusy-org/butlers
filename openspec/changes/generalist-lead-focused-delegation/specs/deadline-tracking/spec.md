## MODIFIED Requirements

### Requirement: Deadline Registration
Butlers SHALL register deadlines via a `deadline_create` MCP tool. A deadline is a scheduled_task with `task_type='deadline'` and additional metadata: `target_date` (date, required), `lead_time_days` (integer, required -- how many days before target_date to begin alerting), `alert_thresholds` (JSONB array of `{days_before: int, severity: string}`, required -- at least one threshold), and `deadline_status` (enum: `pending`, `alerted`, `escalated`, `completed`, `expired`). A `deadline_create` call that carries a `prompt` creates a runtime obligation: it SHALL be an `administer` effect that requires per-event owner approval and is never delegable to a child, the obligation's capability set SHALL be no larger than the creating invocation's grant at creation, and an obligation created under exposure SHALL start every occurrence exposed. `deadline_update` and `deadline_delete` on a prompt-bearing deadline change or suppress an obligation and SHALL apply the same bounds. Because one tool cannot be classed per call in the catalog, `deadline_create`, `deadline_update` and `deadline_delete` SHALL be catalogued as `administer` outright, as the scheduling tools are (`core-scheduler` "Schedule CRUD API").

Source: RFC 0038 D4, D5 and D7
Scope: v1-mandatory

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

#### Scenario: A goal creates a prompt-bearing deadline
- **WHEN** a goal invocation calls `deadline_create` with a `prompt`
- **THEN** the call is an `administer` effect that is parked for per-event owner approval and is refused to a child invocation
- **AND** the created obligation records a capability set no larger than the creating invocation's grant
- **AND** if the creating invocation was exposed, every occurrence of the obligation starts exposed

### Requirement: Deadline Prompt Context Injection
When a deadline task in `prompt` mode fires a threshold, the owning daemon SHALL submit an obligation admission instead of dispatching a prompt to a session, and the admitted goal's input SHALL carry the deadline's original prompt text as its brief plus structured context including `target_date`, `days_remaining`, `fired_threshold` (the threshold that triggered this admission), `deadline_status`, and `all_thresholds` (for the lead to understand the full alert timeline). A job-mode deadline task SHALL keep receiving the same threshold metadata in its job context and SHALL start no goal.

Source: RFC 0038 D2 and D5
Scope: v1-mandatory

#### Scenario: Prompt includes deadline context
- **WHEN** a deadline with `prompt="Begin visa renewal process"` fires its 42-day threshold
- **THEN** the obligation admission's goal input includes the original prompt text plus structured deadline context: `target_date`, `days_remaining=42`, `fired_threshold={days_before: 42, severity: "info"}`, and the full list of thresholds
- **AND** the admitted goal holds no capability beyond the deadline obligation's recorded capability set
