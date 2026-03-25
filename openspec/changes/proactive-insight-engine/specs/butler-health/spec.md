# Health Butler — Insight Scan

## Purpose
Adds an insight-scan scheduled task to the Health butler that generates proactive insight candidates from health domain data.

## MODIFIED Requirements

### Requirement: Health Butler Schedules
The health butler runs health checks, memory jobs, and insight scans.

#### Scenario: Scheduled task inventory
- **WHEN** the health butler daemon is running
- **THEN** it executes: `memory-consolidation` (0 */6 * * *, job), `memory-episode-cleanup` (0 4 * * *, job), and `insight-scan` (0 7 15 * * *, job: evaluate health domain data and generate insight candidates)

## ADDED Requirements

### Requirement: Health Insight Scan Job
The health butler's `insight-scan` job SHALL evaluate health domain data and produce insight candidates covering measurement gaps, medication refill timing, symptom trend alerts, and health streaks. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool — the butler does not write to `shared.insight_candidates` directly.

#### Scenario: Insight-scan job handler registration
- **WHEN** the health butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Measurement gap insights
- **WHEN** the insight-scan job evaluates measurement gaps
- **THEN** it SHALL generate candidates for measurement types where the time since last measurement exceeds 2x the user's typical cadence for that measurement type
- **AND** the typical cadence SHALL be computed as the median interval between the last 10 measurements of that type
- **AND** gaps exceeding 3x the typical cadence SHALL have priority 75
- **AND** gaps exceeding 2x the typical cadence SHALL have priority 55
- **AND** the `dedup_key` SHALL be `health:measurement-gap:{measurement-type}`
- **AND** `expires_at` SHALL be 3 days from generation
- **AND** measurement types with fewer than 3 historical entries SHALL be excluded (insufficient data for cadence computation)

#### Scenario: Medication refill timing insights
- **WHEN** the insight-scan job evaluates active medications
- **THEN** it SHALL generate candidates for medications where dose logging frequency suggests the supply will run out within 14 days (based on prescribed frequency vs logged doses)
- **AND** estimated depletion within 3 days SHALL have priority 90 (time-critical)
- **AND** estimated depletion within 7 days SHALL have priority 75
- **AND** estimated depletion within 14 days SHALL have priority 60
- **AND** the `dedup_key` SHALL be `health:medication-refill:{medication-id}`
- **AND** `expires_at` SHALL be the estimated depletion date
- **AND** medications with `active=false` SHALL be excluded

#### Scenario: Symptom trend alerts
- **WHEN** the insight-scan job evaluates recent symptom logs
- **THEN** it SHALL generate candidates when the same symptom has been logged 3 or more times in the past 7 days with severity >= 3 (on the standard 1-5 scale)
- **AND** priority SHALL be 70 (actionable soon)
- **AND** the `dedup_key` SHALL be `health:symptom-trend:{symptom-name}:{year-week}`
- **AND** `expires_at` SHALL be 3 days from generation
- **AND** the message SHALL include the count and average severity

#### Scenario: Health streak recognition
- **WHEN** the insight-scan job detects a positive health streak
- **THEN** it SHALL generate candidates for notable streaks such as "7 consecutive days of logging meals" or "30 days of daily blood pressure measurements"
- **AND** priority SHALL be 25 (background observation, verbose mode only)
- **AND** the `dedup_key` SHALL be `health:streak:{measurement-type}:{streak-milestone}`
- **AND** `cooldown_days` SHALL be 30
- **AND** `expires_at` SHALL be 7 days from generation
- **AND** streak milestones SHALL be at 7, 30, 60, 90, 180, and 365 days
