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
  `idempotency_key` `google_health:<user>:workout:<id>:<digest>`, where
  `<digest>` fingerprints the session's normalized content
- **AND** a trailing-window re-poll of an unchanged session SHALL reuse the same
  idempotency key
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

#### Scenario: Edited workout supersedes the earlier fact with provenance

- **WHEN** a re-polled workout session has a changed type, times, duration,
  detection or summary metric (or a changed upstream `updateTime`)
- **THEN** the connector SHALL emit a new envelope whose `idempotency_key` carries
  a different `<digest>` and whose `external_event_id` is unchanged
- **AND** the Health ingest SHALL write the new `workout_session` fact, mark the
  earlier active fact for the same session `superseded` with `invalid_at`, set the
  new fact's `supersedes_id`, and record a `memory_links` `supersedes` edge
- **AND** the new fact SHALL be skipped when the latest active version (including a
  legacy bare-key fact) already has identical `valid_at`, content and metadata
- **AND** replaying the current or an older version, or racing versions of one
  session, SHALL leave exactly the latest-written version active, and no ingest
  step SHALL delete a fact; the superseded version is retained until the
  standard superseded-fact purge (`memory_purge_superseded`)
- **AND** Chronicler SHALL keep one workout episode per session, updated to the
  edited values

#### Scenario: Upstream workout deletion is not inferred

- **WHEN** a previously ingested workout is absent from a later list or reconcile
  response
- **THEN** the connector and ingest SHALL change nothing, because the public v4
  reference documents no deletion signal for list or reconcile results
- **AND** deletion SHALL be handled only when a positive upstream signal is
  documented (see `docs/connectors/google_health.md`)
