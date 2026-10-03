## MODIFIED Requirements

### Requirement: Chronicler Compatibility Deferred

The Google Health connector SHALL defer direct raw-event projection to
Chronicler. It SHALL ingest recorded workouts as `workout_session` wellness
envelopes within the read-only scopes already requested. Its sleep, workout, and
daily-summary envelopes continue through the Health fact pipeline, where
Chronicler may read approved durable facts asynchronously after Health `mem_011`
applies its scoped `SELECT` read grant.

ID: REQ-connector-google-health-015
Source: RFC 0014 Amendment 1; [Observed] `src/butlers/connectors/google_health.py`
Scope: v1-mandatory

#### Scenario: Google Health not projected by Chronicler initially

- **WHEN** the Google Health connector emits wellness envelopes
- **THEN** Chronicler SHALL NOT receive those raw connector events directly
- **AND** any Chronicler projection SHALL run asynchronously from its
  approved `health.facts` read surface, enabled by the existing Health
  `mem_011` grant, rather than from a connector route
- **AND** the connector SHALL NOT claim that its envelopes alone establish a
  Chronicler source adapter

#### Scenario: Workout ingestion remains deferred

- **WHEN** the connector polls the `workout` resource (`exercise` data type) and
  a data point carries an interval
- **THEN** it SHALL emit one envelope per data point with
  `external_event_id` `google_health:<user>:workout_session:<id>` and
  `idempotency_key` `google_health:<user>:workout:<id>`
- **AND** a trailing-window re-poll SHALL reuse the same idempotency key
- **AND** the Health ingest SHALL write one `workout_session` fact with
  `valid_at` equal to the session start and metadata `activity_type`,
  `duration_ms`, `end_time`, `session_id`, `detection` (`manual` or `auto`),
  and optional `calories`, `distance_m`, `average_heart_rate`

#### Scenario: Workout polling needs no additional consent

- **WHEN** the workout resource is enabled
- **THEN** the connector SHALL NOT request any scope beyond the three
  read-only families in `GOOGLE_HEALTH_SCOPES`
- **AND** a missing scope or forbidden response SHALL degrade that resource
  through the existing `scope_missing` path, and absence of workouts SHALL NOT
  be read as "no workouts"

#### Scenario: Upstream edits and deletions are not yet reconciled

- **WHEN** a provider workout is edited or removed after it was ingested
- **THEN** the connector SHALL NOT claim to supersede or retract the stored
  fact; this reconciliation is a deferred slice
