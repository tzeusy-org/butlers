## ADDED Requirements

### Requirement: Home Presence Source Health Gate

The deterministic Home presence producer SHALL confirm the Home Assistant
source is healthy before deriving `at_home` from `ha_entity_snapshot`.

#### Scenario: HA outage leaves owner presence unmeasurable

- **WHEN** `run_home_presence_context_producer` runs while
  `ha_source_health` is not `healthy` for `home_assistant`, or no recent
  successful-contact timestamp exists
- **THEN** it SHALL return `presence="unmeasurable"` without querying
  `ha_entity_snapshot`
- **AND** it SHALL neither assert nor clear `at_home`, so any prior signal is
  left to expire through its bounded TTL

#### Scenario: Healthy HA source preserves presence derivation

- **WHEN** `ha_source_health` records a recent `status='healthy'` contact for
  `home_assistant`
- **THEN** the producer SHALL continue to apply its configured-owner,
  freshness, and home-versus-away rules to `ha_entity_snapshot`
