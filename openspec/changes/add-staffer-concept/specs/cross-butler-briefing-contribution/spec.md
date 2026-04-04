## MODIFIED Requirements

### Requirement: Contribution Job Scheduling
Each specialist butler SHALL have a `daily_briefing_contribution` entry in its `butler.toml` with `dispatch_mode="job"`, `job_name="daily_briefing_contribution"`, and cron `55 6 * * *` (06:55 UTC = 14:55 SGT). Staffer-typed agents SHALL NOT register or execute briefing contribution jobs.

#### Scenario: Schedule entry present (butler-typed agents only)
- **WHEN** a butler-typed agent daemon starts and syncs TOML schedules
- **THEN** a `daily_briefing_contribution` scheduled task exists with cron `55 6 * * *` and dispatch_mode `job`

#### Scenario: Staffers excluded from briefing contribution
- **WHEN** a staffer-typed agent daemon starts and syncs TOML schedules
- **THEN** any `daily_briefing_contribution` schedule entries SHALL be skipped during registration
- **AND** the staffer SHALL NOT have a handler in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` for `daily_briefing_contribution`

#### Scenario: Job registered in daemon (butler-typed agents only)
- **WHEN** the scheduler dispatches the `daily_briefing_contribution` job on a butler-typed agent
- **THEN** the job handler is found in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` for the butler's name
