# Dashboard Ingestion Dispatch Console — Runtime-Instance Authority Delta

## ADDED Requirements

### Requirement: Fleet health counts executable runtime instances only

The connectors roster, its attention strip, its fleet-liveness KPIs, and the
cross-connector rollups SHALL count rows whose persisted `operational_role` is
`runtime_instance`, and no others.

#### Scenario: Checkpoint rows are not connectors

- **WHEN** the registry holds one online runtime instance and several
  `checkpoint` rows belonging to it
- **THEN** the roster SHALL list one connector
- **AND** the fleet total, online, stale, and offline counts SHALL each reflect
  that single runtime instance
- **AND** no checkpoint SHALL appear as an offline connector, contribute message
  counters to the fleet error rate, or occupy a slot in the attention strip

#### Scenario: A dead runtime instance is still reported

- **WHEN** a `runtime_instance` row has not heartbeated within the offline
  threshold
- **THEN** it SHALL be counted offline

Excluding storage rows SHALL NOT suppress a genuinely dead process.

### Requirement: Checkpoint history is inspectable under its parent

Checkpoint records SHALL be returned nested under the runtime instance that owns
them, labelled by the stream they track, and SHALL carry no liveness, state, or
health of their own.

#### Scenario: Cursors are nested and labelled

- **WHEN** a connector's checkpoints are returned
- **THEN** each record SHALL appear under its parent connector
- **AND** its label SHALL be the part of the cursor key its parent identity does
  not already account for
- **AND** the record SHALL carry no liveness or state field

#### Scenario: Two accounts never collect each other's cursors

- **WHEN** two identities of the same `connector_type` each own checkpoints
- **THEN** grouping SHALL be keyed on
  `(connector_type, parent_endpoint_identity)`
- **AND** each account SHALL show only its own records

#### Scenario: A checkpoint with no resolvable parent stays visible

- **WHEN** a checkpoint records no parent, or names a parent with no registry
  row
- **THEN** it SHALL be returned in a distinct unparented collection
- **AND** the dashboard SHALL surface that collection

An orphaned cursor is a real condition. Dropping it would trade one
invisibility for another.

### Requirement: Unknown classification is a named unavailable state

A row whose `operational_role` is `unknown` SHALL report a distinct
`unclassified` liveness. It SHALL NOT be reported as active or healthy, and
SHALL NOT be inferred into `offline`.

#### Scenario: An unclassified record reports its own state

- **WHEN** a registry row's role has not been established
- **THEN** its `liveness` SHALL be `unclassified`
- **AND** the roster SHALL render that verdict rather than an online, offline,
  or healthy one

Nothing has claimed the row as a process, so there is no heartbeat contract to
measure it against; naming the gap is the only honest verdict.

#### Scenario: Unclassified records are counted apart from the fleet

- **WHEN** unclassified records are present
- **THEN** they SHALL be reported in their own count
- **AND** they SHALL NOT be included in the fleet total, online, stale, or
  offline counts
- **AND** the roster SHALL still list them, so an unclassified record is
  investigated rather than silently dropped

#### Scenario: A degraded source never fabricates a classification

- **WHEN** the registry query itself fails
- **THEN** the response SHALL set `connector_registry_available` to `false`,
  return an empty connector list, and report zero — including a zero
  unclassified count — rather than a fabricated roster

## Source References

- Non-Negotiable Rule 7 (transport is a connector responsibility) —
  `about/heart-and-soul/vision.md`
- RFC 0003 (Switchboard routing and ingestion)
- Degraded-envelope conventions —
  `docs/api_and_protocols/response-conventions.md`
- Roster and fleet-health contract —
  `openspec/specs/dashboard-ingestion-dispatch-console/spec.md`
- Tracked implementation bead — `bu-6jv4m.11`
