# Cross-Butler Briefing Contribution

## Purpose

Defines the schema by which each butler contributes content to the cross-butler briefing.

## Requirements

### Requirement: Briefing Contribution Schema
Each specialist butler's briefing contribution SHALL be a JSON object conforming to a standard envelope with fields: `butler` (string, butler name), `date` (string, ISO date YYYY-MM-DD), `has_updates` (boolean), `highlights` (array of highlight objects), and `summary` (string, pre-rendered human-readable text). Each highlight object SHALL have `category` (string), `text` (string), and `priority` (string, one of "high", "medium", "low").

#### Scenario: Contribution with updates
- **WHEN** a specialist butler has domain-relevant data for the briefing date
- **THEN** it produces a contribution with `has_updates=true`, at least one highlight, and a non-empty `summary`

#### Scenario: Contribution without updates
- **WHEN** a specialist butler has no noteworthy data for the briefing date
- **THEN** it produces a contribution with `has_updates=false`, an empty `highlights` array, and a non-empty no-update summary (for example `No <domain> updates today.`)

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
The Health butler's `daily_briefing_contribution` job SHALL query its own `facts` rows (scope `health`) to extract: today's missed doses, today's doses due later, the number of non-skipped doses taken today, and the latest weight measurement from the window running from 7 days before owner-local midnight up to now.

"Today" and the current time SHALL be taken in the owner's configured timezone (the shared `settings.general` timezone), falling back to SGT (UTC+8) only when the owner has no usable timezone configured. The day window runs from owner-local midnight to the next owner-local midnight, and the missed / due-later split uses the owner-local current time. The contribution key and envelope `date` remain the fleet-wide SGT briefing date.

A dose is **missed** when its scheduled time today has already passed (at or before now, to the minute) in owner-local time and no non-skipped dose has been logged to cover it; a dose is **due later today** when its scheduled time is still ahead in owner-local time. A medication's scheduled doses come from its `frequency` and `schedule`: as-needed medications and frequencies whose phase cannot be derived never produce a missed or due-later dose, a weekly medication counts only on a weekday named in its schedule (with no time, it counts as missed all day on that weekday), and a daily-type dose with no derivable time counts as missed while untaken. Logged non-skipped doses cover the earliest due doses first. A medication with no dose logged since midnight is therefore NOT automatically missed.

The contribution SHALL carry these highlights, each only when it applies: missed doses (category `medication`, priority `high`, text `Missed dose(s) today: <names>`), doses due later today (category `medication`, priority `low`, text `Doses due later today: <names>`), and latest weight (category `weight`, priority `low`, text `Latest weight: <value>`). The summary SHALL combine `Missed N dose(s) today.` (N is the number of medications with at least one missed dose), `Took N dose(s).` (N is the number of non-skipped doses logged today) and `Weight: <value>.` when they apply, and read `No health updates today.` otherwise. `has_updates` SHALL be true exactly when at least one highlight is present, so a day with only taken doses has `has_updates=false`.

#### Scenario: Health data available
- **WHEN** the Health butler has active medications and a weight measurement from the past 7 days
- **THEN** the contribution includes a missed-doses highlight (priority "high") for medications with an overdue untaken dose, a due-later highlight (priority "low") for medications with a dose still ahead today, and a latest-weight highlight (priority "low")
- **AND** its highlights are exactly the missed-doses, due-later and latest-weight highlights that apply

#### Scenario: A dose is missed only after its time has passed
- **WHEN** a daily medication is scheduled for 06:00 and 20:00, no dose is logged today, and the owner-local time is 06:55
- **THEN** the medication is reported as missed (06:00 has passed) and as due later (20:00 is ahead)

#### Scenario: As-needed medication is never missed
- **WHEN** an active medication is taken as needed and no dose is logged today
- **THEN** it appears in neither the missed nor the due-later highlight

#### Scenario: Day window follows the owner timezone
- **WHEN** the owner's configured timezone is not SGT
- **THEN** doses are counted between owner-local midnight and the next owner-local midnight, and the missed / due-later split uses the owner-local current time
- **AND** when no usable owner timezone is configured, the same computation uses SGT

#### Scenario: No health data
- **WHEN** the Health butler has no missed or due-later doses and no weight measurement in the past 7 days
- **THEN** the contribution has `has_updates=false` and the summary reads `No health updates today.`

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

### Requirement: Lifestyle Butler Contribution
The Lifestyle butler's `daily_briefing_contribution` job SHALL query its memory tables to extract today's taste and consumption activity: volatile consumption facts recorded today under `watches`, `reads`, `plays`, and `listens_to` predicates, plus newly captured stable taste preferences under `likes_genre`, `likes_artist`, `likes_cuisine`, `favorite_restaurant`, `favorite_recipe`, `hobby`, `food_preference`, `food_dislike`, `routine`, `listening_pattern`, `purpose`, and `context`.

#### Scenario: Consumption activity today
- **WHEN** the user has recorded new volatile consumption facts since midnight SGT
- **THEN** the contribution includes one highlight per fact with category "consumption" and priority "low"

#### Scenario: Taste preference captured today
- **WHEN** the user has recorded new stable taste preferences since midnight SGT
- **THEN** the contribution includes one highlight per fact with category "taste-updates" and priority "low"

#### Scenario: No lifestyle highlights
- **WHEN** no new consumption or taste facts were recorded today
- **THEN** the contribution has `has_updates=false`

### Requirement: Contribution Job Scheduling
Each specialist butler SHALL have a `daily_briefing_contribution` entry in its `butler.toml` with `dispatch_mode="job"`, `job_name="daily_briefing_contribution"`, and cron `55 6 * * *` (06:55 in the owner's timezone, which the scheduler reads cron in). The canonical specialist set is defined by `SPECIALIST_BUTLERS` in `src/butlers/jobs/briefing.py`: `education`, `finance`, `health`, `home`, `lifestyle`, `relationship`, `travel`. Staffer-typed agents SHALL NOT register or execute briefing contribution jobs.

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

### Requirement: Delegated Return Wakes Are Not Briefing Contributions

A delegated-answer return wake SHALL be internal continuation work, not a
specialist briefing contribution. It SHALL not write or read a
briefing/daily/<date> state key, general.v_briefing_contributions, a combined
briefing state entry, or any briefing envelope. It SHALL not create a
daily_briefing_contribution schedule entry or deterministic briefing handler.

RFC 0010's read-only, batch aggregation exception SHALL not be reused for
delegation callbacks, task creation, answer lookup, or real-time cross-butler
coordination. The shared delegation ledger and Switchboard MCP route remain the
only permitted v1 cross-butler surfaces for this protocol.

#### Scenario: Valid delegated answer does not enter the briefing composer

- **WHEN** a valid delegate_answer callback creates or reconciles an
  asker-local delegate-return task
- **THEN** no briefing contribution or combined-briefing state SHALL be written
  or read as part of that callback
- **AND** no briefing envelope, composer input, or user-facing delivery SHALL
  be produced by the wake protocol

#### Scenario: Later bounded briefing producer stays independent

- **WHEN** a later deterministic briefing job uses delegate_ask to request
  factual domain context
- **THEN** it SHALL use the same Switchboard-mediated delegation contract
  without direct sibling-schema access
- **AND** the existence of a return task SHALL not authorize same-day composer,
  envelope, quiet-window, or owner-notification behavior

### Requirement: Home Briefing Source Health Gate

The Home butler's `daily_briefing_contribution` job SHALL check Home
Assistant source health before treating snapshot-backed device and environment
inputs as measurable.

#### Scenario: Home Assistant source is unmeasurable

- **WHEN** `ha_source_health` is not `healthy` for `home_assistant`, or no
  recent successful-contact timestamp exists
- **THEN** the contribution SHALL skip the device and environment snapshot
  queries
- **AND** it SHALL include a high-priority highlight stating that Home
  Assistant is unmeasurable instead of reporting a nominal all-clear
- **AND** the job result SHALL set `ha_source_unmeasurable=true`

#### Scenario: Healthy source preserves contribution behavior

- **WHEN** `ha_source_health` records a recent `status='healthy'` contact for
  `home_assistant`
- **THEN** the contribution SHALL continue to query its snapshot-backed device
  and environment inputs according to the Home contribution contract
