## ADDED Requirements

### Requirement: Calendar Reconciles Overcommitment Conditions

A supervised loop in the dashboard-api process SHALL scan the owner's forward calendar window
with the existing `query_calendar_conflicts` read model, unchanged, and reconcile the qualifying
issues into the owner condition ledger, in-process and best-effort, with `source=
"calendar:overcommitment"`. Qualifying issues SHALL be `overlap` and `overloaded_day` issues with
`severity == "warning"`; `back_to_back` issues SHALL NOT qualify. The fingerprint SHALL be
`compute_fingerprint("calendar:overcommitment", 1, facts)` with facts `{"kind": "overlap",
"pair_id": <overlap_pair_id>}` for an overlap and `{"kind": "overloaded_day", "date":
<owner-local YYYY-MM-DD>}` for an overloaded day; severity, event count and hours are evidence,
never identity. The window SHALL be `[owner-local midnight today, + N days)` with N defaulting to
7 and bounded 1..90, the cadence SHALL default to 1800 seconds, and both SHALL be read from the
dashboard-api process environment; the detector thresholds SHALL remain the scan's own defaults.
The loop is a deterministic job and SHALL NOT invoke a model or create an obligation admission.

ID: REQ-owner-condition-ledger-007
Source: archived change generalize-owner-condition-ledger task 6.1; bu-60pwv6.23 shaping note; calendar-conflict-overcommitment-radar
Scope: v1-reserved

`[Unknown]` Whether `calendar_event_instances.id` is stable across a provider resync of the same
instance, and whether a dedup-store outage, which fails open to the default strategy while
`available` stays true, can change which copy survives a cluster and so the pair id (verify
task 1.1). `[Unknown]` Whether a complete scan should also require fresh
`source_freshness` rows (verify task 1.2).

#### Scenario: A double-booked pair opens a condition

- **WHEN** a complete scan inside the window reports an `overlap` issue with severity `warning`
- **THEN** the loop reconciles a `calendar:overcommitment` condition identified by that issue's
  canonical pair id
- **AND** the condition's summary and metadata carry the kind, the owner-local date, the overlap
  length in minutes and the two entry ids, and no event title, attendee or location

#### Scenario: An overloaded day opens one condition per day

- **WHEN** a complete scan reports an `overloaded_day` issue for an owner-local date
- **THEN** the loop reconciles one condition identified by that date, with the total meeting
  hours as evidence
- **AND** a `back_to_back` issue in the same scan opens nothing

#### Scenario: A cleared conflict resolves automatically

- **WHEN** a later complete scan no longer reports an issue that has an active condition (the
  event moved, was cancelled or declined, became transparent, or its day left the window)
- **THEN** that condition resolves by snapshot omission
- **AND** an explicit `resolve_owner_condition` still resolves it earlier, and a still-observed
  identity opens the next episode per the ledger recurrence rule

#### Scenario: A degraded or partial scan never resolves

- **WHEN** `query_calendar_conflicts` returns `available=false` (a total fan-out failure, or a
  partial per-butler failure whose responders returned issues)
- **THEN** the loop reconciles with `snapshot_complete=false` (or skips reconciliation on a
  total failure) so no active condition resolves by omission
- **AND** observed issues from the responding schemas still confirm their conditions

#### Scenario: A reconciliation failure never breaks the loop

- **WHEN** the ledger reconciliation raises (for example the table is unreachable or
  mid-migration) or the Switchboard pool is unavailable
- **THEN** the loop logs a warning and waits for its next tick; the radar endpoint and the other
  loops in the process are unaffected

#### Scenario: Concurrent scans are idempotent

- **WHEN** two scans of the same calendar state reconcile at the same time
- **THEN** the per-source advisory lock serializes them and the deterministic fingerprints yield
  confirmations, never a duplicate active episode

### Requirement: Home Reconciles Device Threshold Conditions

The Home butler's `device_health_check` scheduled job SHALL reconcile device threshold crossings
into the owner condition ledger, in-process and best-effort, alongside (not instead of) its
existing notification path, with `source="home:device-threshold"`. The fingerprint SHALL be
`compute_fingerprint("home:device-threshold", 1, {"entity_id": <entity_id>, "issue":
"battery" | "offline"})`. Qualifying inputs SHALL be exactly the battery and offline
classifications the job already produces from `ha_entity_snapshot` whose severity is `critical`
or `warning`, using the thresholds in the state store under `home:thresholds:*`; an `info`
battery issue (default at or below 30%) SHALL NOT qualify, and the producer SHALL add no
threshold of its own. Severity and the current level SHALL be evidence, not identity; offline
issues SHALL be keyed without a level. A battery entity whose state is not numeric
(`unavailable`, `unknown`) SHALL carry its active battery condition forward with `confirm_only`
and SHALL NOT resolve it without a numeric reading, and the producer SHALL NOT reconcile when
the job returns early on an empty `ha_entity_snapshot`.

ID: REQ-owner-condition-ledger-008
Source: archived change generalize-owner-condition-ledger task 6.2; bu-60pwv6.23 shaping note; bu-60pwv6.24
Scope: v1-reserved

`[Unknown]` Whether a stale snapshot `last_updated` on a battery sensor should count as
unmeasured instead of low (verify task 2.1). `[decision]` Crossings are detected at job cadence
(every 4 hours), not at the Home Assistant write path.

#### Scenario: A low battery opens a condition

- **WHEN** `run_device_health_check` classifies an entity's battery as `warning` or `critical`
- **THEN** it reconciles a `home:device-threshold` condition for that entity with `issue=
  "battery"`, in addition to the existing notification behavior

#### Scenario: A recovered or replaced device resolves

- **WHEN** a later run, with a healthy Home Assistant source, reads the same entity's battery
  as a number above its warning threshold (including an `info`-level reading such as 25%) or no
  longer lists it as offline
- **THEN** the complete snapshot no longer observes it and its condition resolves

#### Scenario: An unavailable battery sensor never resolves its condition

- **WHEN** an entity with an active `battery` condition reports a state that is not numeric
  (`unavailable` or `unknown`), so the job produces no battery issue for it
- **THEN** the producer carries that condition forward with `confirm_only` and does not
  resolve it, until a numeric reading above the warning threshold is seen
- **AND** an `info`-severity battery issue never opens a condition

#### Scenario: An empty entity snapshot does not reconcile

- **WHEN** `run_device_health_check` finds `ha_entity_snapshot` empty and returns early
- **THEN** the producer performs no reconciliation and every active condition stays active

#### Scenario: An offline escalation keeps one episode

- **WHEN** an offline device moves from `warning` to `critical`
- **THEN** the same `{entity_id, "offline"}` episode is confirmed with the new level as
  evidence, and no second episode opens

#### Scenario: An unmeasurable Home Assistant source never resolves

- **WHEN** `_require_ha_source_healthy` raises `HASourceUnmeasurableError`, so the job skips its
  checks
- **THEN** the producer performs no reconciliation for that run, and every active condition
  stays active

#### Scenario: A reconciliation failure never breaks the device health check

- **WHEN** the ledger reconciliation raises
- **THEN** `run_device_health_check` logs a warning and continues; its notification and memory
  fact behavior for that run is unaffected

#### Scenario: Condition text carries no more than the owner surface shows

- **WHEN** a condition is reconciled
- **THEN** its summary and metadata carry the entity id, a friendly name when one exists, the
  issue kind, the severity and, for battery only, the percentage level, and no other
  attribute of the entity

### Requirement: Finance Reconciles Budget Threshold Conditions

The Finance butler's `insight-scan` scheduled job SHALL reconcile budget threshold crossings into
the owner condition ledger, in-process and best-effort, alongside its existing cooldown-gated
budget insight candidate, with `source="finance:budget-threshold"`. The fingerprint SHALL be
`compute_fingerprint("finance:budget-threshold", 1, {"category": <category>, "period":
<period>, "period_start": <ISO date>})`. Qualifying inputs SHALL be the `budget_status()` items
whose status is `warning` or `exceeded`; the owner-configured warn and alert thresholds of each
budget remain the only thresholds. The status SHALL be evidence, not identity, so a warning that
crosses into exceeded within the same window confirms and updates the same episode.

ID: REQ-owner-condition-ledger-010
Source: archived change generalize-owner-condition-ledger task 6.2; bu-60pwv6.23 shaping note; bu-60pwv6.24; finance-insight budget thresholds
Scope: v1-reserved

#### Scenario: Crossing a budget's warn threshold opens a condition

- **WHEN** `run_insight_scan` observes a `budget_status()` item with status `warning`
- **THEN** it reconciles a `finance:budget-threshold` condition identified by the category,
  period and period start, in addition to the existing candidate behavior

#### Scenario: Escalation to exceeded updates the same episode

- **WHEN** a later scan in the same window observes that item with status `exceeded`
- **THEN** the same episode is confirmed with the new status as evidence, and no second episode
  opens

#### Scenario: A new period or a lower total resolves

- **WHEN** a later complete scan no longer observes the item (the period rolled over, the budget
  was raised or removed, or a transaction was deleted)
- **THEN** its condition resolves

#### Scenario: A failing budget status never resolves

- **WHEN** `budget_status()` raises
- **THEN** the producer skips this source's reconciliation (or passes `snapshot_complete=false`)
  and no active condition resolves

#### Scenario: A reconciliation failure never breaks the insight scan

- **WHEN** the ledger reconciliation raises
- **THEN** `run_insight_scan` logs a warning and continues; insight-candidate submission and the
  other two ledger sources for that run are unaffected
