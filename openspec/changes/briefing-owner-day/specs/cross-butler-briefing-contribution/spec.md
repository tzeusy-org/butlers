## MODIFIED Requirements

### Requirement: Contribution State Key Convention

ID: REQ-cross-butler-briefing-contribution-002
Source: bu-5ii4gj.5.1 (run 26 move 5)
Scope: v1-mandatory
Each specialist butler SHALL write its daily briefing contribution to its state store under the key `briefing/daily/<YYYY-MM-DD>` where the date is the current date in the owner's configured timezone (falling back to SGT (UTC+8) when none is usable) at the time of job execution.

#### Scenario: Key written on normal execution
- **WHEN** the `daily_briefing_contribution` job runs
- **THEN** the contribution JSON is written to the butler's state store under key `briefing/daily/<today-in-owner-zone>`

#### Scenario: Key overwrites stale entry
- **WHEN** the job runs and a contribution for today's date already exists
- **THEN** the existing entry is overwritten via `state_set` (upsert semantics)

### Requirement: Health Butler Contribution

ID: REQ-cross-butler-briefing-contribution-003
Source: bu-5ii4gj.5.1 (run 26 move 5)
Scope: v1-mandatory
The Health butler's `daily_briefing_contribution` job SHALL query its own `facts` rows (scope `health`) to extract: today's missed doses, today's doses due later, the number of non-skipped doses taken today, and the latest weight measurement from the window running from 7 days before owner-local midnight up to now.

"Today" and the current time SHALL be taken in the owner's configured timezone (the shared `settings.general` timezone), falling back to SGT (UTC+8) only when the owner has no usable timezone configured. The day window runs from owner-local midnight to the next owner-local midnight, and the missed / due-later split uses the owner-local current time. The contribution key and envelope `date` are the same owner-zone date, so every contributor and the aggregation job key a day identically.

A dose is **missed** when its scheduled time today has already passed (at or before now, to the minute) in owner-local time and no non-skipped dose has been logged to cover it; a dose is **due later today** when its scheduled time is still ahead in owner-local time. A medication's scheduled doses come from its `frequency` and `schedule`: as-needed medications and frequencies whose phase cannot be derived never produce a missed or due-later dose, a weekly medication counts only on a weekday named in its schedule (with no time, it is due later until 21:00 owner-local on that weekday, then missed while untaken), and a daily-type dose with no derivable time is due later until 21:00 owner-local, then counts as missed while untaken. Logged non-skipped doses cover the earliest due doses first. A medication with no dose logged since midnight is therefore NOT automatically missed.

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
