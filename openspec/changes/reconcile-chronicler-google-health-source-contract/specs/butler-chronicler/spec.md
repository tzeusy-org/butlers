## MODIFIED Requirements

### Requirement: Source Compatibility Declarations

Every source adapter SHALL declare its chronicler compatibility status before
projection runs against it. A supported Health source is a read-only projection
of durable `health.facts` evidence after the Health memory `mem_011` migration
has established its table-specific `SELECT` grant for
`butler_chronicler_rw`, not direct delivery of a raw external event to
Chronicler.

ID: REQ-butler-chronicler-007
Source: RFC 0014 Amendment 1; [Observed] `src/butlers/chronicler/contracts.py`
Scope: v1-mandatory

#### Scenario: Initial supported sources

- **WHEN** Chronicler starts for the first time
- **THEN** `core.sessions` SHALL be declared `supported`
- **AND** `google_calendar.completed` SHALL be declared `supported`
- **AND** `spotify.session_summary` SHALL be declared `deferred` unless a
  durable evidence surface exists
- **AND** `google_health.measurements` SHALL be declared `supported` for
  Health fact projections
- **AND** `health.steps` and `health.heart_rate` SHALL each be declared
  `supported` for their Health fact projections

#### Scenario: Health fact projection is not direct connector ingest

- **WHEN** a supported Health source is projected after its Health memory
  `mem_011` read prerequisite is applied
- **THEN** Chronicler SHALL read only its approved `health.facts` surface on
  its scheduled adapter path
- **AND** that surface SHALL be accessed through the existing, migration-
  tracked `mem_011` `SELECT` grant rather than a direct runtime ACL fallback
- **AND** Chronicler SHALL NOT receive or retain the raw Google Health
  connector envelope as a projection input
- **AND** source availability SHALL remain independent of connector heartbeat
  status and of any direct connector-to-Chronicler route

#### Scenario: Optional schema guard

- **WHEN** a source adapter's optional schema is missing on the current
  deployment
- **THEN** the adapter SHALL mark its state as inactive with the missing-
  schema reason
- **AND** SHALL NOT raise an unhandled exception
- **AND** the missing-schema state SHALL be visible via
  `source_adapter_state`
