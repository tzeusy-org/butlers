## ADDED Requirements

### Requirement: HA Source Health Guard for Snapshot Readers

The implementation SHALL provide the behavior described by this requirement.
Reading `ha_entity_snapshot` alone cannot tell a caller whether Home Assistant
is currently reachable — a snapshot captured during an outage is re-stamped
with a fresh `captured_at` on every persistence cycle and looks identical to
a genuinely current one. Before trusting `ha_entity_snapshot`, job handlers
and the generic snapshot reader SHALL check `ha_source_health` (maintained by
the Home Assistant module's "HA Source Health Recording" requirement) and
treat the source as unmeasurable rather than healthy whenever its status is
not `'healthy'` or no health record exists at all.

#### Scenario: Generic reader guards on source health

- **WHEN** `_read_entity_snapshot` is called
- **THEN** it SHALL first check `ha_source_health` for `'home_assistant'`
- **AND** it SHALL raise `HASourceUnmeasurableError` (carrying the last known
  `last_success_at`, or `None` if never recorded) when the status is not
  `'healthy'` or no row exists, before querying `ha_entity_snapshot`

#### Scenario: Job entry points skip on an unmeasurable source

- **WHEN** `run_energy_digest`, `run_device_health_check`, or
  `run_environment_report` runs and `ha_source_health` shows the source is
  not `'healthy'` (an active outage) or has no recorded contact
- **THEN** the job SHALL send an owner notification distinct from the
  existing "entity snapshot empty" alert, naming the last good contact
  timestamp (or "never")
- **AND** it SHALL return `{"error": "ha_source_unmeasurable", "last_good_at":
  <timestamp-or-None>}` without querying `ha_entity_snapshot` for
  domain data

#### Scenario: Healthy source proceeds as before

- **WHEN** `ha_source_health` shows `status='healthy'` for `'home_assistant'`
- **THEN** the reader or job handler SHALL proceed to query
  `ha_entity_snapshot` exactly as it did before this requirement existed
  (including the pre-existing empty-snapshot handling)
