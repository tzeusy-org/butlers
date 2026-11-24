## ADDED Requirements

### Requirement: Workout Detection Distinguishes Owner-Started Sessions

The Google Health connector SHALL record how each workout was captured, so that downstream readers can tell an owner-started session from a passively detected one. `detection` SHALL be `manual` for recording method `MANUAL`, `active` for `ACTIVELY_MEASURED`, and `auto` for every other method, including a missing one.

ID: REQ-connector-google-health-017
Source: bu-r3fmja.6; deep dive 2026-11-03 [Observed] `src/butlers/connectors/google_health.py` maps every method except `MANUAL` to `auto`
Scope: v1-mandatory

#### Scenario: A workout started on the watch
- **WHEN** a workout arrives with recording method `ACTIVELY_MEASURED`
- **THEN** its normalized record SHALL carry `detection = "active"`

#### Scenario: A passively detected walk
- **WHEN** a workout arrives with recording method `PASSIVELY_MEASURED` or no recording method
- **THEN** its normalized record SHALL carry `detection = "auto"`
