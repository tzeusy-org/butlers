# Cross-Butler Briefing Aggregation

## Purpose
Defines the General butler's aggregation job that reads specialist briefing contributions cross-schema, merges them into a combined briefing payload, and makes that payload available for the EOD prompt. Covers the cross-schema SQL view, aggregation logic, combined output format, and database migration requirements.

## ADDED Requirements

### Requirement: Cross-Schema Briefing View
A SQL view `general.v_briefing_contributions` SHALL exist that provides read-only access to briefing contribution state entries across all specialist schemas. The view SHALL union `butler`, `key`, and `value` columns from the `state` table of each specialist schema (health, finance, relationship, travel, education, home) filtered to keys matching `briefing/daily/%`. Each UNION term SHALL include an explicit `butler` column as a string literal identifying the source schema (e.g., `SELECT 'health' AS butler, key, value FROM health.state WHERE key LIKE 'briefing/daily/%'`).

This view is a sanctioned exception to schema isolation (RFC 0006). Constraints: the view is read-only, uses an explicit `butler` source column for auditability, queries are date-filtered only, a health check validates view accessibility, and grants are migration-based (auditable).

#### Scenario: View returns contributions from all specialists
- **WHEN** multiple specialist butlers have written briefing contributions for today
- **THEN** querying `general.v_briefing_contributions` returns all contributions with their source schema identifiable via the `butler` column
- **AND** the `butler` column value is a string literal set per UNION term (not derived from the JSON payload)

#### Scenario: View returns empty when no contributions exist
- **WHEN** no specialist butlers have written briefing contributions
- **THEN** querying `general.v_briefing_contributions` returns zero rows

#### Scenario: View is read-only
- **WHEN** an INSERT, UPDATE, or DELETE is attempted on the view
- **THEN** the operation fails (views on UNION queries are not updatable in PostgreSQL)

#### Scenario: Aggregation validates butler source column matches payload
- **WHEN** the aggregation job reads a contribution from the view
- **THEN** it validates that `value::jsonb->>'butler'` matches the `butler` source column
- **AND** if they do not match, the contribution is treated as malformed and skipped with a warning log

### Requirement: Aggregation Job
The General butler SHALL have a `collect_briefing_contributions` deterministic job that reads all specialist contributions for today's date (SGT) via the `v_briefing_contributions` view, merges them into a combined payload, and writes the result to General's state store under key `briefing/combined/<YYYY-MM-DD>`.

#### Scenario: All specialists contributed
- **WHEN** the aggregation job runs and all 6 specialist contributions exist for today
- **THEN** the combined payload contains entries from all 6 butlers, ordered by butler name

#### Scenario: Partial contributions
- **WHEN** the aggregation job runs and only 3 of 6 specialists have contributed
- **THEN** the combined payload contains the 3 available contributions
- **AND** the `missing_butlers` field lists the names of butlers that did not contribute

#### Scenario: No contributions available
- **WHEN** the aggregation job runs and no specialist contributions exist for today
- **THEN** the combined payload has an empty `contributions` array and `missing_butlers` lists all 6 specialist butler names

#### Scenario: Malformed contribution skipped
- **WHEN** a specialist's state entry exists but contains invalid JSON or is missing required fields
- **THEN** the aggregation job logs a warning and excludes that contribution from the combined payload
- **AND** the butler is listed in `missing_butlers`

### Requirement: Combined Briefing Payload Schema
The combined briefing payload written by the aggregation job SHALL be a JSON object with fields: `date` (string, ISO date), `generated_at` (string, ISO datetime with timezone), `contributions` (array of valid contribution objects), and `missing_butlers` (array of butler name strings for butlers that did not contribute or had malformed data).

#### Scenario: Combined payload structure
- **WHEN** the aggregation job completes successfully
- **THEN** the state entry at `briefing/combined/<date>` contains all required fields
- **AND** each entry in `contributions` conforms to the contribution schema (has `butler`, `date`, `has_updates`, `highlights`, `summary`)

### Requirement: Aggregation Job Scheduling
The General butler SHALL have a `collect_briefing_contributions` entry in its `butler.toml` with `dispatch_mode="job"`, `job_name="collect_briefing_contributions"`, and cron `58 6 * * *` (06:58 UTC = 14:58 SGT).

#### Scenario: Schedule entry present
- **WHEN** the General butler daemon starts and syncs TOML schedules
- **THEN** a `collect_briefing_contributions` scheduled task exists with cron `58 6 * * *` and dispatch_mode `job`

#### Scenario: Job registered in daemon
- **WHEN** the scheduler dispatches the `collect_briefing_contributions` job
- **THEN** the job handler is found in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` under the `general` butler name

### Requirement: Aggregation View Migration
An Alembic migration SHALL create the `general.v_briefing_contributions` view and grant SELECT on each specialist schema's `state` table to the database role used by the General butler. The migration SHALL be reversible (downgrade drops the view and revokes grants).

#### Scenario: Migration upgrade
- **WHEN** the Alembic migration is applied
- **THEN** the view `general.v_briefing_contributions` exists and is queryable
- **AND** SELECT grants on specialist `state` tables are active

#### Scenario: Migration downgrade
- **WHEN** the Alembic migration is reverted
- **THEN** the view `general.v_briefing_contributions` is dropped
- **AND** cross-schema SELECT grants are revoked
