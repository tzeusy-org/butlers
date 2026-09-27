## MODIFIED Requirements

### Requirement: Chronicler Compatibility Deferred

The Google Health connector SHALL defer direct raw-event projection to
Chronicler and SHALL defer Google Health workout ingestion. Its supported
sleep and daily-summary envelopes continue through the Health fact pipeline,
where Chronicler may read approved durable facts asynchronously after Health
`mem_011` applies its scoped `SELECT` read grant.

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

- **WHEN** the connector runs its configured resource bundles
- **THEN** it SHALL NOT emit a workout resource or a wellness envelope that
  maps to `workout_session`
- **AND** it SHALL NOT write a `workout_session` Health fact through its
  current ingest contract
- **AND** the existence of a Chronicler adapter for a separately present
  `workout_session` fact SHALL NOT be represented as Google Health connector
  workout support
