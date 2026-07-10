# Chronicler Telemetry Distillation

## Purpose

Give Chronicler a scheduled, deterministic batch-projection path from the
telemetry that already lands durably in `connectors.filtered_events` /
`connectors.owntracks_points` (Home Assistant non-person domains, OwnTracks
GPS) into activity-shaped episodes, and materialize daily per-lane rollups
with deterministic anomaly flags — so high-volume telemetry that is
correctly `skip`-routed at ingestion is no longer also unread downstream.
No per-event LLM invocation anywhere in this capability (RFC 0014 §D5); the
one optional LLM call is bounded to once per local day over already-reduced
output.

## ADDED Requirements

### Requirement: HA Non-Person Sensor Activity Projection

The system SHALL provide a deterministic projection adapter,
`home_assistant.sensor_activity`, that reads Home Assistant state-change
events for non-`person` domains from `connectors.filtered_events` and
projects ambient activity signal into Chronicler, without invoking an LLM.

#### Scenario: Motion runs project as room activity evidence

- **WHEN** the adapter observes a contiguous run of `binary_sensor`
  `device_class=motion` "on" states for one entity
- **THEN** it SHALL upsert a `room_activity_episode` with `layer=evidence`
- **AND** the episode SHALL carry `source_name`, `source_ref`, `precision`,
  and evidence provenance identical in shape to every other projection
  adapter's output

#### Scenario: Door/entry transitions project as point events

- **WHEN** the adapter observes a `binary_sensor` state change with
  `device_class` in `{door, garage_door, opening}`
- **THEN** it SHALL upsert an `entry_event` point event (instantaneous, not
  an episode)

#### Scenario: Evidence promotes to activity only with a corroborator

- **WHEN** a `room_activity_episode` overlaps an independent corroborating
  signal (an enabled routine window, an `occupation_block`, or a Spotify
  listening episode)
- **THEN** the reconciliation pass SHALL be the sole path that promotes the
  aggregate to `layer=activity, confidence=low`
- **AND** an uncorroborated `room_activity_episode` SHALL remain
  `layer=evidence` and SHALL NOT be counted in any time/balance aggregate

#### Scenario: New sensor-activity episodes never claim the Work lane

- **WHEN** `aggregations.category_for` classifies a
  `home_assistant.sensor_activity` episode
- **THEN** it SHALL resolve to a category that maps to the `rest` (or
  equivalent ambient) lane
- **AND** it SHALL NOT resolve to the `work`/`occupation` category under any
  condition

#### Scenario: Unclassified allow-listed domains are left untouched

- **WHEN** the adapter observes an event from an allow-listed domain not
  covered by its rule table (e.g. `climate`, `lock`, `cover`, `script`,
  `automation`)
- **THEN** it SHALL NOT project any row for that event
- **AND** the event SHALL remain exactly as available today (queryable only
  via `connectors.filtered_events` directly)

#### Scenario: No per-event LLM invocation

- **WHEN** the adapter's `project()` method executes
- **THEN** it SHALL NOT invoke an LLM at any point
- **AND** guardrail tests SHALL assert this via the standard
  `ProjectionAdapter._llm_probe` hook

### Requirement: OwnTracks GPS Place Clustering

The system SHALL provide a deterministic projection adapter,
`owntracks.place_cluster`, that clusters stationary runs of
`connectors.owntracks_points` into labeled `place_episode` rows, independent
of and complementary to any Wi-Fi-SSID-based presence adapter.

#### Scenario: Stationary point run forms a place cluster

- **WHEN** a contiguous run of OwnTracks points falls within a fixed radius
  for at least a minimum dwell duration
- **THEN** the adapter SHALL upsert a `place_episode` covering that span

#### Scenario: Recurring clusters label against owner-declared reference points

- **WHEN** a cluster's centroid falls within a labeling threshold distance of
  an owner-declared reference point (e.g. home)
- **THEN** the `place_episode` SHALL carry that label
- **AND** no external geocoding service or LLM call SHALL be used to derive
  the label

#### Scenario: Unlabeled recurring clusters surface honestly

- **WHEN** a cluster recurs across multiple days but matches no
  owner-declared reference point
- **THEN** the `place_episode` SHALL be labeled `place_unknown`
- **AND** it SHALL still be stored and queryable, not discarded

#### Scenario: Independent source name from any SSID-based adapter

- **WHEN** both `owntracks.place_cluster` and a separate Wi-Fi-SSID presence
  adapter are active
- **THEN** each SHALL use its own distinct `source_name`
- **AND** both MAY independently appear as corroborators for
  `occupation_inferred` without one superseding the other

### Requirement: Daily Rollup Materialization

The system SHALL provide a scheduled job that materializes one row per local
calendar day per activity lane into `chronicler.daily_rollups`, using the
same counting rules the live aggregate API already applies, so the two
surfaces can never diverge.

#### Scenario: Rollup reuses the live counting rules exactly

- **WHEN** the rollup job computes lane totals for a closed local day
- **THEN** it SHALL call `aggregations.lane_for_activity` and
  `aggregations.union_seconds` directly
- **AND** it SHALL NOT implement any parallel or divergent counting logic

#### Scenario: Rollup output matches the live endpoint

- **WHEN** the rollup for a given closed local day is compared against a
  same-window call to the live `aggregate/by-category` endpoint
- **THEN** the per-lane second totals SHALL match exactly

#### Scenario: Only fully-elapsed local days are rolled up

- **WHEN** the rollup job runs
- **THEN** it SHALL only materialize a local calendar day once that day's
  local-timezone window has fully elapsed
- **AND** a partial/in-progress local day SHALL NOT be materialized

#### Scenario: Idempotent re-materialization

- **WHEN** the rollup job re-runs for a local date it has already
  materialized (e.g. after a late-arriving correction/override)
- **THEN** it SHALL upsert on `(local_date, lane)` rather than creating
  duplicate rows

### Requirement: Deterministic Anomaly Flags

The system SHALL evaluate a fixed set of deterministic anomaly rules against
each day's rollup, distinguishing a known feeder outage from a genuine
behavioral anomaly before emitting any flag.

#### Scenario: Feeder outage flags as an outage, not a behavior

- **WHEN** a source's `source_adapter_state.active = false` or its checkpoint
  is stale beyond twice its scheduled cron interval
- **THEN** the rollup for that day SHALL carry a `feeder_dark` flag for that
  source
- **AND** no behavioral flag (e.g. `sleep_missing`) that depends on the same
  source's data SHALL be emitted for that day

#### Scenario: Genuine behavioral anomaly flags when the feeder is healthy

- **WHEN** a lane's tracked time is zero or its share deviates sharply from
  its trailing-14-day median, **AND** every source contributing to that lane
  reports `active = true` with a fresh checkpoint
- **THEN** the corresponding behavioral flag (`sleep_missing`,
  `routine_break`, or `lane_share_outlier`) SHALL be emitted

#### Scenario: Low-evidence days do not produce spurious outlier flags

- **WHEN** a local day's total tracked activity-layer seconds fall below a
  minimum-evidence floor
- **THEN** `lane_share_outlier` SHALL NOT be emitted for that day regardless
  of the resulting lane-share ratio

#### Scenario: Flags are passive and queryable only

- **WHEN** any anomaly flag is written
- **THEN** the system SHALL NOT send a proactive notification as a result
- **AND** the flag SHALL be visible only via a query/read API surface

### Requirement: Bounded Once-Daily LLM Labeling (Optional)

The system SHALL bound any LLM labeling of a day's rollup to at most one call per local day, and this capability MAY be entirely disabled: disabling it SHALL NOT change the correctness or completeness of the deterministic rollup or flags.

#### Scenario: At most one LLM call per local day

- **WHEN** the labeling pass runs for a local day
- **THEN** it SHALL invoke the LLM at most once for that day
- **AND** its input SHALL be limited to that day's `daily_rollups`/
  `daily_rollup_flags` rows and top episode titles, never raw sensor/point
  event rows

#### Scenario: Disabling the labeling pass preserves correctness

- **WHEN** the owner disables the LLM labeling pass
- **THEN** `daily_rollups` and `daily_rollup_flags` SHALL remain fully
  populated and correct
- **AND** only the narrative/label fields SHALL be absent

#### Scenario: Narration is exposed on the read API surface when present

- **WHEN** a client reads a materialized day through the rollups read API and a
  day summary and/or per-flag label was written by the labeling pass
- **THEN** the response SHALL include the day's prose summary and each labeled
  flag's one-line label alongside the deterministic rollup/flag fields

#### Scenario: Absent narration reads as a normal null, never an error

- **WHEN** a client reads a day whose labeling pass has not run (disabled, not
  yet run, or a day predating the feature)
- **THEN** the narrative/label fields SHALL be returned as absent (null)
- **AND** the response SHALL NOT signal a degraded/error source state solely
  because narration is absent — the deterministic rollup and flag fields remain
  fully present and trustworthy
