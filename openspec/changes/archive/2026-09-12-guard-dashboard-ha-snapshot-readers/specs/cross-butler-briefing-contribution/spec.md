## ADDED Requirements

### Requirement: Home Briefing Source Health Gate

The Home butler's `daily_briefing_contribution` job SHALL check Home
Assistant source health before treating snapshot-backed device and environment
inputs as measurable.

#### Scenario: Home Assistant source is unmeasurable

- **WHEN** `ha_source_health` is not `healthy` for `home_assistant`, or no
  recent successful-contact timestamp exists
- **THEN** the contribution SHALL skip the device and environment snapshot
  queries
- **AND** it SHALL include a high-priority highlight stating that Home
  Assistant is unmeasurable instead of reporting a nominal all-clear
- **AND** the job result SHALL set `ha_source_unmeasurable=true`

#### Scenario: Healthy source preserves contribution behavior

- **WHEN** `ha_source_health` records a recent `status='healthy'` contact for
  `home_assistant`
- **THEN** the contribution SHALL continue to query its snapshot-backed device
  and environment inputs according to the Home contribution contract
