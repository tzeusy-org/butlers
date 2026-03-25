## ADDED Requirements

### Requirement: Briefing Contribution Schema
Each specialist butler's briefing contribution SHALL be a JSON object conforming to a standard envelope with fields: `butler` (string, butler name), `date` (string, ISO date YYYY-MM-DD), `has_updates` (boolean), `highlights` (array of highlight objects), and `summary` (string, pre-rendered human-readable text). Each highlight object SHALL have `category` (string), `text` (string), and `priority` (string, one of "high", "medium", "low").

#### Scenario: Contribution with updates
- **WHEN** a specialist butler has domain-relevant data for the briefing date
- **THEN** it produces a contribution with `has_updates=true`, at least one highlight, and a non-empty `summary`

#### Scenario: Contribution without updates
- **WHEN** a specialist butler has no noteworthy data for the briefing date
- **THEN** it produces a contribution with `has_updates=false`, an empty `highlights` array, and an empty `summary`

#### Scenario: Invalid contribution rejected
- **WHEN** a contribution is missing required fields (`butler`, `date`, `has_updates`)
- **THEN** the aggregation layer SHALL treat it as malformed and skip it with a warning log

### Requirement: Contribution State Key Convention
Each specialist butler SHALL write its daily briefing contribution to its state store under the key `briefing/daily/<YYYY-MM-DD>` where the date is the current date in SGT (UTC+8) at the time of job execution.

#### Scenario: Key written on normal execution
- **WHEN** the `daily_briefing_contribution` job runs
- **THEN** the contribution JSON is written to the butler's state store under key `briefing/daily/<today-SGT>`

#### Scenario: Key overwrites stale entry
- **WHEN** the job runs and a contribution for today's date already exists
- **THEN** the existing entry is overwritten via `state_set` (upsert semantics)

### Requirement: Contribution Cleanup
Each specialist butler's `daily_briefing_contribution` job SHALL delete contribution state entries older than 7 days to prevent state store bloat.

#### Scenario: Old entries cleaned up
- **WHEN** the `daily_briefing_contribution` job completes its contribution write
- **THEN** it deletes all state entries matching `briefing/daily/*` where the date suffix is more than 7 days before today (SGT)

#### Scenario: No old entries to clean
- **WHEN** there are no contribution entries older than 7 days
- **THEN** the cleanup step completes without error (no-op)

### Requirement: Health Butler Contribution
The Health butler's `daily_briefing_contribution` job SHALL query its domain tables to extract: latest weight entry, medication adherence for today, any missed doses, and the next upcoming appointment.

#### Scenario: Health data available
- **WHEN** the Health butler has medication records and appointments
- **THEN** the contribution includes highlights for missed doses (priority "high"), adherence percentage (priority "medium"), and next appointment (priority "low")

#### Scenario: No health data
- **WHEN** the Health butler has no medication, weight, or appointment data for the relevant period
- **THEN** the contribution has `has_updates=false`

### Requirement: Finance Butler Contribution
The Finance butler's `daily_briefing_contribution` job SHALL query its domain tables to extract: bills due within 48 hours, spending anomalies (transactions exceeding 2x the rolling category average), and subscription renewals within the current week.

#### Scenario: Bills due soon
- **WHEN** there are bills due within 48 hours of the briefing date
- **THEN** the contribution includes a highlight per bill with priority "high" and category "bills"

#### Scenario: Spending anomaly detected
- **WHEN** a transaction in the last 24 hours exceeds 2x the 30-day rolling average for its category
- **THEN** the contribution includes a highlight with priority "medium" and category "spending"

#### Scenario: No financial highlights
- **WHEN** no bills are due, no anomalies detected, and no subscription renewals this week
- **THEN** the contribution has `has_updates=false`

### Requirement: Relationship Butler Contribution
The Relationship butler's `daily_briefing_contribution` job SHALL query its domain tables to extract: birthdays within the next 7 days, follow-ups due today or overdue, and contacts with interaction gaps exceeding their configured threshold.

#### Scenario: Birthday upcoming
- **WHEN** a contact has a birthday within 7 days
- **THEN** the contribution includes a highlight with priority "medium" and category "birthday"

#### Scenario: Overdue follow-up
- **WHEN** a follow-up is due today or overdue
- **THEN** the contribution includes a highlight with priority "high" and category "follow-up"

#### Scenario: No relationship highlights
- **WHEN** no birthdays, follow-ups, or interaction gaps are relevant
- **THEN** the contribution has `has_updates=false`

### Requirement: Travel Butler Contribution
The Travel butler's `daily_briefing_contribution` job SHALL query its domain tables to extract: departures within 48 hours, check-in windows opening today, and any missing travel documents.

#### Scenario: Departure within 48 hours
- **WHEN** a trip departure is within 48 hours of the briefing date
- **THEN** the contribution includes a highlight with priority "high" and category "departure"

#### Scenario: Missing travel document
- **WHEN** an upcoming trip has a missing required document (visa, passport expiry)
- **THEN** the contribution includes a highlight with priority "high" and category "document"

#### Scenario: No travel highlights
- **WHEN** no departures, check-ins, or document issues are relevant
- **THEN** the contribution has `has_updates=false`

### Requirement: Education Butler Contribution
The Education butler's `daily_briefing_contribution` job SHALL query its domain tables to extract: pending spaced-repetition review count, learning streak status, and current active topic.

#### Scenario: Reviews pending
- **WHEN** there are spaced-repetition reviews due today
- **THEN** the contribution includes a highlight with the review count, priority "medium", and category "reviews"

#### Scenario: Streak at risk
- **WHEN** the learning streak will break if no review is completed today and the streak is >= 3 days
- **THEN** the contribution includes a highlight with priority "high" and category "streak"

#### Scenario: No education highlights
- **WHEN** no reviews are pending and no streak is at risk
- **THEN** the contribution has `has_updates=false`

### Requirement: Home Butler Contribution
The Home butler's `daily_briefing_contribution` job SHALL query its domain tables to extract: active device alerts, environment sensor outliers (temperature, humidity outside configured bounds), and energy consumption anomalies.

#### Scenario: Device alert active
- **WHEN** a Home Assistant device has an active alert/warning state
- **THEN** the contribution includes a highlight with priority "high" and category "device-alert"

#### Scenario: Environment outlier
- **WHEN** a sensor reading is outside configured normal bounds
- **THEN** the contribution includes a highlight with priority "medium" and category "environment"

#### Scenario: No home highlights
- **WHEN** no device alerts, environment outliers, or energy anomalies are present
- **THEN** the contribution has `has_updates=false`

### Requirement: Contribution Job Scheduling
Each specialist butler SHALL have a `daily_briefing_contribution` entry in its `butler.toml` with `dispatch_mode="job"`, `job_name="daily_briefing_contribution"`, and cron `55 6 * * *` (06:55 UTC = 14:55 SGT).

#### Scenario: Schedule entry present
- **WHEN** the specialist butler daemon starts and syncs TOML schedules
- **THEN** a `daily_briefing_contribution` scheduled task exists with cron `55 6 * * *` and dispatch_mode `job`

#### Scenario: Job registered in daemon
- **WHEN** the scheduler dispatches the `daily_briefing_contribution` job
- **THEN** the job handler is found in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` for the butler's name
