# Health Butler Specification

The Health butler tracks personal health data including measurements, medications, conditions, diet, symptoms, and research. It is designed for longitudinal health tracking, providing tools to log data over time and generate summaries and trend reports. The Health butler runs on port 40103 with its own dedicated database `butler_health`. It has no modules initially -- all functionality is provided via butler-specific MCP tools.

---

## Database Schema

```sql
CREATE TABLE measurements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT NOT NULL,
    value JSONB NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE medications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    dosage TEXT NOT NULL,
    frequency TEXT NOT NULL,
    schedule JSONB NOT NULL DEFAULT '[]',
    active BOOLEAN NOT NULL DEFAULT true,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE medication_doses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    medication_id UUID NOT NULL REFERENCES medications(id),
    taken_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    skipped BOOLEAN NOT NULL DEFAULT false,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conditions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    diagnosed_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE meals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    nutrition JSONB,
    eaten_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE symptoms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 10),
    condition_id UUID REFERENCES conditions(id),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE research (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags JSONB NOT NULL DEFAULT '[]',
    source_url TEXT,
    condition_id UUID REFERENCES conditions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## ADDED Requirements

### Requirement: Butler Configuration

The Health butler SHALL be configured with `name = "health"`, `port = 40103`, and database name `butler_health` in its `butler.toml`. The Health butler SHALL have no modules initially -- all domain-specific tools are registered as butler-specific MCP tools.

#### Scenario: Health butler starts with correct identity

WHEN the Health butler starts with its `butler.toml`,
THEN the butler name SHALL be "health",
AND the port SHALL be 40103,
AND the database SHALL be `butler_health`.

#### Scenario: Health butler has no modules

WHEN the Health butler starts,
THEN the module list SHALL be empty,
AND only core tools and butler-specific health tools SHALL be registered.

---

### Requirement: Butler-Specific Migration

The Health butler SHALL have Alembic revisions in the `health` version chain that create the `measurements`, `medications`, `medication_doses`, `conditions`, `meals`, `symptoms`, and `research` tables as defined in the database schema above. These revisions SHALL be applied after the core Alembic chain during the daemon startup sequence.

#### Scenario: Health tables created on first startup

WHEN the Health butler starts against a freshly provisioned database,
THEN the database SHALL contain the `measurements`, `medications`, `medication_doses`, `conditions`, `meals`, `symptoms`, and `research` tables,
AND all tables SHALL have the columns and constraints defined in the schema.

---

### Requirement: Measurement logging via measurement_log

The `measurement_log` MCP tool SHALL accept `type` (string), `value` (JSONB), and optional `notes` (string) and `measured_at` (timestamp) parameters. It SHALL insert a new row into the `measurements` table.

The `type` parameter MUST be one of: `weight`, `blood_pressure`, `heart_rate`, `blood_sugar`, `temperature`. If an unrecognized type is provided, the tool MUST reject the request with an error.

The `value` parameter SHALL be a JSONB object whose structure depends on the measurement type (e.g., `{"kg": 75.5}` for weight, `{"systolic": 120, "diastolic": 80}` for blood pressure). The tool SHALL store the value as-is without schema enforcement beyond JSONB validity.

If `measured_at` is not provided, it SHALL default to the current time.

#### Scenario: Log a weight measurement

WHEN `measurement_log` is called with `type="weight"`, `value={"kg": 75.5}`,
THEN a new row SHALL be inserted into the `measurements` table with `type="weight"` and `value={"kg": 75.5}`,
AND `measured_at` SHALL default to the current time.

#### Scenario: Log a blood pressure measurement with timestamp

WHEN `measurement_log` is called with `type="blood_pressure"`, `value={"systolic": 120, "diastolic": 80}`, and `measured_at="2026-02-09T08:00:00Z"`,
THEN a new row SHALL be inserted with the specified type, value, and timestamp.

#### Scenario: Reject unrecognized measurement type

WHEN `measurement_log` is called with `type="cholesterol"`,
THEN the tool SHALL return an error indicating the measurement type is not recognized,
AND no row SHALL be inserted.

---

### Requirement: Measurement history via measurement_history

The `measurement_history` MCP tool SHALL accept `type` (string) and optional `start_date` and `end_date` (timestamp) parameters. It SHALL return all measurements matching the given type, filtered by date range if provided, ordered by `measured_at` descending.

#### Scenario: Retrieve all weight measurements

WHEN `measurement_history` is called with `type="weight"`,
THEN the tool SHALL return all rows from `measurements` where `type="weight"`, ordered by `measured_at` descending.

#### Scenario: Retrieve measurements within a date range

WHEN `measurement_history` is called with `type="blood_pressure"`, `start_date="2026-01-01T00:00:00Z"`, and `end_date="2026-01-31T23:59:59Z"`,
THEN the tool SHALL return only blood pressure measurements with `measured_at` within the specified range.

#### Scenario: No measurements found

WHEN `measurement_history` is called with `type="heart_rate"` and no heart rate measurements exist,
THEN the tool SHALL return an empty list,
AND it MUST NOT raise an error.

---

### Requirement: Latest measurement via measurement_latest

The `measurement_latest` MCP tool SHALL accept a `type` (string) parameter and return the single most recent measurement of that type, based on the `measured_at` column.

#### Scenario: Latest weight measurement exists

WHEN `measurement_latest` is called with `type="weight"` and multiple weight measurements exist,
THEN the tool SHALL return only the measurement with the most recent `measured_at` value.

#### Scenario: No measurement of the given type exists

WHEN `measurement_latest` is called with `type="temperature"` and no temperature measurements exist,
THEN the tool SHALL return null,
AND it MUST NOT raise an error.

---

### Requirement: Medication management via medication_add

The `medication_add` MCP tool SHALL accept `name` (string), `dosage` (string), `frequency` (string), and optional `schedule` (JSONB array of time strings) and `notes` (string) parameters. It SHALL insert a new row into the `medications` table with `active=true`.

The `schedule` parameter SHALL be a JSON array of time strings (e.g., `["08:00", "20:00"]`) representing the times of day the medication should be taken. If not provided, it SHALL default to an empty array.

#### Scenario: Add a new medication with schedule

WHEN `medication_add` is called with `name="Metformin"`, `dosage="500mg"`, `frequency="twice daily"`, and `schedule=["08:00", "20:00"]`,
THEN a new row SHALL be inserted into `medications` with `active=true` and the specified schedule,
AND the tool SHALL return the medication's UUID.

#### Scenario: Add a medication without schedule

WHEN `medication_add` is called with `name="Ibuprofen"`, `dosage="200mg"`, `frequency="as needed"`,
THEN a new row SHALL be inserted with `schedule='[]'` and `active=true`.

---

### Requirement: Medication listing via medication_list

The `medication_list` MCP tool SHALL accept an optional `active_only` (boolean, default true) parameter. It SHALL return medications from the `medications` table. When `active_only` is true, only medications with `active=true` SHALL be returned. When `active_only` is false, all medications SHALL be returned.

#### Scenario: List active medications

WHEN `medication_list` is called with no parameters,
THEN the tool SHALL return only medications where `active=true`.

#### Scenario: List all medications including inactive

WHEN `medication_list` is called with `active_only=false`,
THEN the tool SHALL return all medications regardless of active status.

#### Scenario: No medications exist

WHEN `medication_list` is called and the `medications` table is empty,
THEN the tool SHALL return an empty list.

---

### Requirement: Dose logging via medication_log_dose

The `medication_log_dose` MCP tool SHALL accept `medication_id` (UUID), optional `taken_at` (timestamp), optional `skipped` (boolean, default false), and optional `notes` (string) parameters. It SHALL insert a new row into the `medication_doses` table.

If `taken_at` is not provided, it SHALL default to the current time. The `skipped` flag SHALL be used to record missed doses -- when `skipped=true`, the dose is recorded as not taken.

If the `medication_id` does not reference an existing medication, the tool MUST return an error.

#### Scenario: Log a taken dose

WHEN `medication_log_dose` is called with a valid `medication_id`,
THEN a new row SHALL be inserted into `medication_doses` with `skipped=false` and `taken_at` set to the current time.

#### Scenario: Log a skipped dose

WHEN `medication_log_dose` is called with a valid `medication_id` and `skipped=true`,
THEN a new row SHALL be inserted with `skipped=true`,
AND the row SHALL record the time the dose was skipped.

#### Scenario: Invalid medication ID rejected

WHEN `medication_log_dose` is called with a `medication_id` that does not exist in the `medications` table,
THEN the tool SHALL return an error indicating the medication was not found,
AND no row SHALL be inserted into `medication_doses`.

---

### Requirement: Medication dose history via medication_history

The `medication_history` MCP tool SHALL accept `medication_id` (UUID) and optional `start_date` and `end_date` (timestamp) parameters. It SHALL return all dose records for the given medication from the `medication_doses` table, ordered by `taken_at` descending.

The tool SHALL also compute and include an adherence rate: the percentage of logged doses where `skipped=false` out of total logged doses within the queried period.

#### Scenario: Retrieve dose history with adherence rate

WHEN `medication_history` is called with a valid `medication_id` and the medication has 10 logged doses where 8 were taken and 2 were skipped,
THEN the tool SHALL return all 10 dose records ordered by `taken_at` descending,
AND the response SHALL include an adherence rate of 80%.

#### Scenario: Retrieve dose history within a date range

WHEN `medication_history` is called with a valid `medication_id`, `start_date`, and `end_date`,
THEN the tool SHALL return only dose records with `taken_at` within the specified range,
AND the adherence rate SHALL be computed only from doses within that range.

#### Scenario: No doses logged

WHEN `medication_history` is called for a medication with no logged doses,
THEN the tool SHALL return an empty list,
AND the adherence rate SHALL be null or 0%.

---

### Requirement: Condition management via condition_add

The `condition_add` MCP tool SHALL accept `name` (string), optional `status` (string, default "active"), optional `diagnosed_at` (timestamp), and optional `notes` (string) parameters. It SHALL insert a new row into the `conditions` table.

The `status` parameter MUST be one of: `active`, `managed`, `resolved`. If an invalid status is provided, the tool MUST reject the request with an error.

#### Scenario: Add a new condition

WHEN `condition_add` is called with `name="Type 2 Diabetes"` and `status="active"`,
THEN a new row SHALL be inserted into `conditions` with the given name and status,
AND the tool SHALL return the condition's UUID.

#### Scenario: Add a condition with default status

WHEN `condition_add` is called with `name="Migraine"` and no status specified,
THEN the condition SHALL be created with `status="active"`.

#### Scenario: Reject invalid status

WHEN `condition_add` is called with `status="unknown"`,
THEN the tool SHALL return an error indicating the status is invalid,
AND no row SHALL be inserted.

---

### Requirement: Condition listing via condition_list

The `condition_list` MCP tool SHALL accept an optional `status` (string) filter parameter. If provided, only conditions matching the given status SHALL be returned. If omitted, all conditions SHALL be returned. Results SHALL be ordered by `created_at` descending.

#### Scenario: List all conditions

WHEN `condition_list` is called with no parameters,
THEN the tool SHALL return all conditions ordered by `created_at` descending.

#### Scenario: List conditions filtered by status

WHEN `condition_list` is called with `status="active"`,
THEN the tool SHALL return only conditions where `status="active"`.

---

### Requirement: Condition updates via condition_update

The `condition_update` MCP tool SHALL accept `id` (UUID) and optional `status` (string) and `notes` (string) parameters. At least one optional field MUST be provided.

If `status` is provided, it MUST be one of: `active`, `managed`, `resolved`. If invalid, the tool MUST reject the request with an error and no update SHALL be applied.

If the `id` does not match an existing condition, the tool MUST return a not-found error.

The tool SHALL update `updated_at` to the current time on every successful update.

#### Scenario: Update condition status to resolved

WHEN `condition_update` is called with a valid `id` and `status="resolved"`,
THEN the condition's status SHALL be updated to "resolved",
AND `updated_at` SHALL be set to the current time.

#### Scenario: Update non-existent condition

WHEN `condition_update` is called with an `id` that does not exist,
THEN the tool SHALL return a not-found error.

#### Scenario: Reject invalid status on update

WHEN `condition_update` is called with `status="cured"`,
THEN the tool SHALL return an error indicating the status is invalid,
AND no changes SHALL be applied.

---

### Requirement: Meal logging via meal_log

The `meal_log` MCP tool SHALL accept `type` (string), `description` (string), optional `nutrition` (JSONB), optional `eaten_at` (timestamp), and optional `notes` (string) parameters. It SHALL insert a new row into the `meals` table.

The `type` parameter MUST be one of: `breakfast`, `lunch`, `dinner`, `snack`. If an invalid type is provided, the tool MUST reject the request with an error.

The `nutrition` parameter SHALL be a JSONB object representing nutritional data (e.g., `{"calories": 450, "protein_g": 25, "carbs_g": 60, "fat_g": 15}`). If not provided, it SHALL default to null.

If `eaten_at` is not provided, it SHALL default to the current time.

#### Scenario: Log a meal with nutrition data

WHEN `meal_log` is called with `type="lunch"`, `description="Grilled chicken salad"`, and `nutrition={"calories": 450, "protein_g": 30}`,
THEN a new row SHALL be inserted into `meals` with the specified values.

#### Scenario: Log a snack without nutrition data

WHEN `meal_log` is called with `type="snack"` and `description="Apple"`,
THEN a new row SHALL be inserted with `nutrition=null` and `eaten_at` defaulting to the current time.

#### Scenario: Reject invalid meal type

WHEN `meal_log` is called with `type="brunch"`,
THEN the tool SHALL return an error indicating the meal type is invalid,
AND no row SHALL be inserted.

---

### Requirement: Meal history via meal_history

The `meal_history` MCP tool SHALL accept optional `type` (string), `start_date`, and `end_date` (timestamp) parameters. It SHALL return meals from the `meals` table matching the filters, ordered by `eaten_at` descending.

#### Scenario: Retrieve all meals

WHEN `meal_history` is called with no parameters,
THEN the tool SHALL return all meals ordered by `eaten_at` descending.

#### Scenario: Retrieve meals filtered by type and date range

WHEN `meal_history` is called with `type="dinner"`, `start_date`, and `end_date`,
THEN the tool SHALL return only dinner meals within the specified date range.

---

### Requirement: Nutrition summary via nutrition_summary

The `nutrition_summary` MCP tool SHALL accept `start_date` and `end_date` (timestamp) parameters. It SHALL aggregate nutritional data from all meals within the date range that have non-null `nutrition` JSONB values.

The summary SHALL include total and daily average calories, protein, carbohydrates, and fat over the period, computed from the `nutrition` JSONB fields across matching meals. Meals without nutrition data SHALL be excluded from the aggregation.

#### Scenario: Summarize nutrition over a week

WHEN `nutrition_summary` is called with a one-week date range and meals within that range have nutrition data,
THEN the tool SHALL return totals and daily averages for calories, protein, carbohydrates, and fat.

#### Scenario: No meals with nutrition data in range

WHEN `nutrition_summary` is called and no meals in the date range have nutrition data,
THEN the tool SHALL return zeroes for all aggregated values,
AND it MUST NOT raise an error.

---

### Requirement: Symptom logging via symptom_log

The `symptom_log` MCP tool SHALL accept `name` (string), `severity` (integer, 1-10), optional `condition_id` (UUID), optional `occurred_at` (timestamp), and optional `notes` (string) parameters. It SHALL insert a new row into the `symptoms` table.

The `severity` parameter MUST be an integer between 1 and 10 inclusive. If outside this range, the tool MUST reject the request with an error.

If `condition_id` is provided, it MUST reference an existing condition. If the condition does not exist, the tool MUST return an error.

If `occurred_at` is not provided, it SHALL default to the current time.

#### Scenario: Log a symptom linked to a condition

WHEN `symptom_log` is called with `name="Headache"`, `severity=6`, and a valid `condition_id`,
THEN a new row SHALL be inserted into `symptoms` with the specified values and the condition link.

#### Scenario: Log a symptom without condition link

WHEN `symptom_log` is called with `name="Fatigue"`, `severity=4`, and no `condition_id`,
THEN a new row SHALL be inserted with `condition_id=null`.

#### Scenario: Reject invalid severity

WHEN `symptom_log` is called with `severity=0` or `severity=11`,
THEN the tool SHALL return an error indicating severity must be between 1 and 10,
AND no row SHALL be inserted.

#### Scenario: Reject invalid condition reference

WHEN `symptom_log` is called with a `condition_id` that does not exist in the `conditions` table,
THEN the tool SHALL return an error indicating the condition was not found,
AND no row SHALL be inserted.

---

### Requirement: Symptom history via symptom_history

The `symptom_history` MCP tool SHALL accept optional `start_date` and `end_date` (timestamp) parameters. It SHALL return all symptoms within the date range, ordered by `occurred_at` descending.

#### Scenario: Retrieve all symptoms

WHEN `symptom_history` is called with no parameters,
THEN the tool SHALL return all symptoms ordered by `occurred_at` descending.

#### Scenario: Retrieve symptoms within a date range

WHEN `symptom_history` is called with `start_date` and `end_date`,
THEN the tool SHALL return only symptoms with `occurred_at` within the specified range.

---

### Requirement: Symptom search via symptom_search

The `symptom_search` MCP tool SHALL accept optional `name` (string), `min_severity` (integer), `max_severity` (integer), `start_date`, and `end_date` (timestamp) parameters. It SHALL return symptoms matching all provided filters, ordered by `occurred_at` descending.

Filters SHALL be combined with AND logic. If no filters are provided, all symptoms SHALL be returned.

#### Scenario: Search symptoms by name

WHEN `symptom_search` is called with `name="Headache"`,
THEN the tool SHALL return all symptoms where `name` matches "Headache" (case-insensitive).

#### Scenario: Search symptoms by severity range

WHEN `symptom_search` is called with `min_severity=7` and `max_severity=10`,
THEN the tool SHALL return only symptoms with `severity` between 7 and 10 inclusive.

#### Scenario: Search with combined filters

WHEN `symptom_search` is called with `name="Nausea"`, `min_severity=5`, and a date range,
THEN the tool SHALL return only symptoms matching all three criteria.

#### Scenario: No matching symptoms

WHEN `symptom_search` is called with filters that match no symptoms,
THEN the tool SHALL return an empty list,
AND it MUST NOT raise an error.

---

### Requirement: Research saving via research_save

The `research_save` MCP tool SHALL accept `title` (string), `content` (string), optional `tags` (JSONB array of strings), optional `source_url` (string), and optional `condition_id` (UUID) parameters. It SHALL insert a new row into the `research` table.

If `tags` is not provided, it SHALL default to an empty array. If `condition_id` is provided, it MUST reference an existing condition.

#### Scenario: Save a research article with tags and condition link

WHEN `research_save` is called with `title="Metformin and Longevity"`, `content="..."`, `tags=["diabetes", "longevity"]`, `source_url="https://example.com/article"`, and a valid `condition_id`,
THEN a new row SHALL be inserted into `research` with all specified values,
AND the tool SHALL return the research entry's UUID.

#### Scenario: Save research without optional fields

WHEN `research_save` is called with only `title` and `content`,
THEN a new row SHALL be inserted with `tags='[]'`, `source_url=null`, and `condition_id=null`.

#### Scenario: Reject invalid condition reference

WHEN `research_save` is called with a `condition_id` that does not exist,
THEN the tool SHALL return an error indicating the condition was not found,
AND no row SHALL be inserted.

---

### Requirement: Research search via research_search

The `research_search` MCP tool SHALL accept optional `query` (string), `tags` (JSONB array of strings), and `condition_id` (UUID) parameters. It SHALL return research entries matching the provided filters, ordered by `created_at` descending.

The `query` parameter SHALL perform a case-insensitive text search against the `title` and `content` fields. The `tags` parameter SHALL match entries whose `tags` array contains any of the provided tags. Filters SHALL be combined with AND logic.

#### Scenario: Search research by query

WHEN `research_search` is called with `query="diabetes"`,
THEN the tool SHALL return research entries where `title` or `content` contains "diabetes" (case-insensitive).

#### Scenario: Search research by tags

WHEN `research_search` is called with `tags=["longevity"]`,
THEN the tool SHALL return research entries whose `tags` array contains "longevity".

#### Scenario: Search research by condition

WHEN `research_search` is called with a valid `condition_id`,
THEN the tool SHALL return research entries linked to that condition.

#### Scenario: No matching research

WHEN `research_search` is called with filters that match no entries,
THEN the tool SHALL return an empty list,
AND it MUST NOT raise an error.

---

### Requirement: Research summarization via research_summarize

The `research_summarize` MCP tool SHALL accept an optional `condition_id` (UUID) or optional `tags` (JSONB array of strings) parameter to scope the research entries to summarize. It SHALL return a structured summary of the matching research entries, including the count of entries, the list of unique tags across all matches, and the titles of the included articles.

This tool provides a quick overview of saved research. The actual summarization of content is intended to be performed by the runtime instance using the returned data.

#### Scenario: Summarize all research for a condition

WHEN `research_summarize` is called with a valid `condition_id`,
THEN the tool SHALL return a summary including the count of research entries linked to that condition, the unique tags across those entries, and their titles.

#### Scenario: Summarize research by tags

WHEN `research_summarize` is called with `tags=["diabetes"]`,
THEN the tool SHALL return a summary of all research entries tagged with "diabetes".

#### Scenario: Summarize all research

WHEN `research_summarize` is called with no parameters,
THEN the tool SHALL return a summary of all research entries in the database.

---

### Requirement: Health summary via health_summary

The `health_summary` MCP tool SHALL accept no parameters. It SHALL return a comprehensive snapshot of the user's current health state, including:

- The latest measurement for each measurement type (weight, blood_pressure, heart_rate, blood_sugar, temperature).
- All active medications with their dosage, frequency, and schedule.
- All active conditions with their status.

#### Scenario: Full health snapshot

WHEN `health_summary` is called and the database contains measurements, active medications, and active conditions,
THEN the response SHALL include the most recent measurement for each type that has data,
AND the response SHALL include all medications where `active=true`,
AND the response SHALL include all conditions where `status="active"`.

#### Scenario: Health summary with sparse data

WHEN `health_summary` is called and only weight measurements and one active condition exist,
THEN the response SHALL include the latest weight measurement,
AND measurement types with no data SHALL be omitted or null,
AND the active condition SHALL be included,
AND the medications list SHALL be empty.

---

### Requirement: Trend report via trend_report

The `trend_report` MCP tool SHALL accept an optional `period` parameter (string, one of: `week`, `month`, default `week`). It SHALL return trend data over the specified period, including:

- Measurement trends: all measurements grouped by type within the period, with first and last values for comparison.
- Medication adherence: for each active medication, the adherence rate (percentage of non-skipped doses) over the period.
- Symptom frequency: count of symptoms grouped by name over the period.
- Symptom severity averages: average severity per symptom name over the period.

#### Scenario: Weekly trend report

WHEN `trend_report` is called with `period="week"`,
THEN the tool SHALL return trend data covering the past 7 days,
AND the response SHALL include measurement trends, medication adherence rates, and symptom frequency and severity data for that period.

#### Scenario: Monthly trend report

WHEN `trend_report` is called with `period="month"`,
THEN the tool SHALL return trend data covering the past 30 days.

#### Scenario: Trend report with no data

WHEN `trend_report` is called and no measurements, doses, or symptoms exist within the period,
THEN the tool SHALL return empty trend data for all categories,
AND it MUST NOT raise an error.

#### Scenario: Invalid period rejected

WHEN `trend_report` is called with `period="year"`,
THEN the tool SHALL return an error indicating the period must be "week" or "month".

---

### Requirement: Medication Reminder Check Scheduled Task

The Health butler SHALL define a `medication-reminder-check` scheduled task that runs three times daily at 8:00 AM, 12:00 PM, and 8:00 PM. This task SHALL be defined in the butler's `butler.toml` as three separate `[[butler.schedule]]` entries (one per time) or as a single entry with a cron expression covering all three times.

When triggered, the task prompt SHALL instruct the runtime instance to check for active medications with scheduled times falling within the next 2 hours that have not yet had a dose logged for the current scheduled time. The runtime instance SHALL use the butler's MCP tools (`medication_list`, `medication_history`) to identify medications due and report any that are missing a dose log.

#### Scenario: Medication reminder at 8 AM

WHEN the `medication-reminder-check` task fires at 8:00 AM,
THEN the spawned runtime instance SHALL check for active medications with schedule times between 08:00 and 10:00,
AND for each such medication, it SHALL check whether a dose has been logged for today covering that time window,
AND it SHALL report any medications that are due but not yet logged.

#### Scenario: All medications already logged

WHEN the `medication-reminder-check` task fires and all medications due within the next 2 hours already have a logged dose,
THEN the runtime instance SHALL report that no reminders are needed.

---

### Requirement: Weekly Health Summary Scheduled Task

The Health butler SHALL define a `weekly-health-summary` scheduled task that runs every Sunday at 9:00 AM. This task SHALL be defined in the butler's `butler.toml` as a `[[butler.schedule]]` entry with `cron = "0 9 * * 0"`.

When triggered, the task prompt SHALL instruct the runtime instance to generate a comprehensive weekly health summary using the butler's MCP tools. The summary SHALL include:

- Weight trend over the past week (if weight measurements exist).
- Medication adherence rates for each active medication over the past week.
- Symptom frequency and patterns over the past week.
- Any notable changes or patterns identified from the data.

#### Scenario: Weekly summary with full data

WHEN the `weekly-health-summary` task fires on Sunday at 9:00 AM,
THEN the spawned runtime instance SHALL use `trend_report(period="week")`, `health_summary`, and other tools as needed,
AND it SHALL generate a summary covering weight trends, medication adherence, and symptom patterns for the past 7 days.

#### Scenario: Weekly summary with no data

WHEN the `weekly-health-summary` task fires and no health data has been logged in the past week,
THEN the runtime instance SHALL report that no data is available for the weekly summary.

---

### Requirement: Referential Integrity for Condition Links

All tables that reference `condition_id` (symptoms, research) SHALL enforce referential integrity via a foreign key constraint to the `conditions` table. Tools that accept a `condition_id` parameter MUST validate that the referenced condition exists before inserting a row.

Deleting a condition is not supported by the current tool set. Conditions can only have their status changed via `condition_update`. This ensures that symptom and research records linked to a condition always have a valid reference.

#### Scenario: Symptom linked to valid condition

WHEN `symptom_log` is called with a `condition_id` that exists in the `conditions` table,
THEN the symptom SHALL be inserted with the condition link intact.

#### Scenario: Research linked to valid condition

WHEN `research_save` is called with a `condition_id` that exists in the `conditions` table,
THEN the research entry SHALL be inserted with the condition link intact.

#### Scenario: Invalid condition_id rejected on symptom

WHEN `symptom_log` is called with a `condition_id` that does not exist,
THEN the tool SHALL return an error,
AND no row SHALL be inserted.

#### Scenario: Invalid condition_id rejected on research

WHEN `research_save` is called with a `condition_id` that does not exist,
THEN the tool SHALL return an error,
AND no row SHALL be inserted.
