# Butler Chronicler — Spec delta for chronicler-telemetry-distillation

## ADDED Requirements

RFC 0038 D2 leaves these projection adapters and their retention/health checks
deterministic. Narration is separately admitted as a declared Chronicler
obligation goal under D5/D18; that change does not move adapter reads into a
model or widen their approved read surfaces, privacy/retention boundaries,
tenant scope, source provenance or source-stamp/taint authority under D6.

### Requirement: Retention-Window-Aware Projection Adapters

A projection adapter whose read surface has a rolling retention policy (a TTL/partition-drop window, as opposed to chronicler's own schema or a TTL-free connector table) SHALL monitor its checkpoint watermark against that table's retention cutoff and surface a warning before source data ages out unprojected.

#### Scenario: Adapter reads a TTL-bearing table

- **WHEN** a projection adapter's read surface is a connector table subject
  to periodic partition pruning (e.g. `connectors.filtered_events`)
- **THEN** the adapter SHALL expose a lag metric comparing its checkpoint
  watermark to the oldest still-retained partition/row
- **AND** the metric SHALL be visible via the same `source_adapter_state`
  surface every other adapter's health is visible through

#### Scenario: Watermark approaches the retention cutoff

- **WHEN** an adapter's checkpoint watermark falls within a configured
  safety margin of the retention cutoff for its read surface
- **THEN** the system SHALL surface a warning (via `source_adapter_state` or
  an equivalent health signal) before any unprojected source data is dropped
  by the retention sweep

#### Scenario: TTL-free read surfaces are unaffected

- **WHEN** a projection adapter reads chronicler's own schema or a
  TTL-free connector table (the existing convention for every adapter
  predating this requirement)
- **THEN** no retention-lag monitoring obligation applies to that adapter

## MODIFIED Requirements

### Requirement: Owner-Day Narrative Composition

Chronicler SHALL compose bounded day-close recaps from supported owner activity
with explicit uncertainty, preserving producer ownership and machine provenance.

ID: REQ-butler-chronicler-023
Source: bu-uhbhzq.2 slices 4-5; owner decision run24-outage-clause = D
Scope: v1-mandatory

#### Scenario: Day-close describes supported owner activity

- **WHEN** the bounded day-close bundle contains source inventories alongside
  supported owner-day observations
- **THEN** the recap SHALL foreground supported place, movement, sleep and
  interaction evidence with explicit uncertainty rather than raw source counts
- **AND** automated Butler processing SHALL NOT be narrated as owner activity
- **AND** inbound email alone SHALL NOT establish social interaction, and an
  owner outbound message alone SHALL NOT establish a two-way exchange
- **AND** playback overlapping recorded sleep SHALL be background or uncertain
  playback, never confirmed listening
- **AND** unknown places, destinations, travel modes and evidence gaps SHALL
  remain unknown, with machine provenance retained independently of prose
- **AND** this narration view SHALL NOT alter the underlying source producers,
  time aggregates, privacy projection or local-day coverage authority
- **AND** recognized delivery-status/tool-summary bookkeeping from the exact
  scheduled day-close SHALL NOT enter automatic session-episode memory; session
  audit and legitimate corrections or retrospective interactions SHALL remain intact

#### Scenario: Uncaptured lanes appear as unknowns

Owner decision `run24-outage-clause` = D (bead bu-uhbhzq.2 notes).

- **WHEN** the day's frozen source availability marks a lane uncaptured
- **THEN** the bundle's `owner_day.unknowns` SHALL carry one deterministic line
  for that lane, for example "Meals not captured (source retired since 13 Aug)",
  in addition to the standing partial-evidence line
- **AND** the line SHALL be stored in the day's unknowns every day, while the
  delivered recap SHALL mention a dark lane on its first dark day and weekly
  after, and a retired or unconfigured lane only once (`owner_day.recap_mention`)
- **AND** the bundle SHALL NOT carry endpoint, source or connector identity
