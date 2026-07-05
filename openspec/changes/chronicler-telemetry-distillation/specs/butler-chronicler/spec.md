# Butler Chronicler — Spec delta for chronicler-telemetry-distillation

## ADDED Requirements

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
