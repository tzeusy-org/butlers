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

### Requirement: Awaiting first heartbeat is an additive unknown-row presentation

The connector APIs SHALL preserve `liveness: "unclassified"` and every existing
unclassified count for all `operational_role = 'unknown'` rows. Within that
umbrella, they SHALL expose the content-blind presentation derived by
`connector-base-spec` as an additive nullable field:

```text
presentation_state: "awaiting_first_heartbeat" | "unclassified" | null
```

`null` applies outside the `unknown` role. The field SHALL be optional for wire
compatibility with responses cached before this addition.

The API additions SHALL be:

- `GET /api/ingestion/connectors/summaries`: `presentation_state` on each
  connector and top-level `awaiting_first_heartbeat_count`;
- `GET /api/ingestion/connectors/cross-summary`:
  `connectors_awaiting_first_heartbeat`;
- `GET /api/switchboard/connectors` and connector detail:
  `presentation_state` on each `ConnectorEntry`; and
- `GET /api/switchboard/connectors/summary`:
  `awaiting_first_heartbeat_count`.

Both awaiting counts SHALL be subsets of the corresponding existing
`unclassified_count`, `connectors_unclassified`, or `unknown_count`; awaiting
rows SHALL remain excluded from runtime-instance fleet totals and health
rollups.

The roster verdict SHALL read **“awaiting first heartbeat”** with supporting
copy **“Setup evidence is present. Runtime status is unavailable until this
connector sends its first heartbeat.”** It SHALL use setup/pending semantics,
not online, stale, offline, healthy, degraded, or error semantics. A bare
`unclassified` row SHALL keep its existing honest unclassified verdict.

#### Scenario: Awaiting presentation preserves the unclassified umbrella

- **WHEN** a returned `unknown` row carries
  `presentation_state = "awaiting_first_heartbeat"`
- **THEN** its `liveness` SHALL remain `unclassified`
- **AND** it SHALL increment both the existing unclassified count and the new
  awaiting-first-heartbeat subset count
- **AND** it SHALL NOT increment total, online, stale, offline, or healthy
  counts
- **AND** the roster SHALL render the exact awaiting verdict and supporting
  copy without claiming that a process is configured, started, or healthy

#### Scenario: Review setup is optional and catalog-backed

- **WHEN** the connector catalog is available and a catalog profile matching
  the awaiting row's `connector_type` supplies its supported Passport provider
- **THEN** the roster MAY render a **“Review setup”** action
- **AND** its destination SHALL be `/secrets?focus=u:<provider>`, derived from
  the matched catalog profile's `provider` field under the existing dormant
  connector setup contract
- **AND** activating it SHALL only navigate to the existing setup surface; it
  SHALL NOT assign an operational role, invoke a provider, mutate a credential,
  or claim to start, restart, run, or recover the connector

#### Scenario: Missing catalog capability does not invent an action

- **WHEN** the connector catalog is loading, unavailable, or has no matching
  profile with a supported setup destination
- **THEN** the awaiting verdict and supporting copy SHALL remain visible from
  registry evidence
- **AND** the **“Review setup”** action SHALL be absent
- **AND** catalog unavailability SHALL use the existing degraded-source
  presentation rather than a connector-type-derived or generic recovery link

#### Scenario: Registry degradation clears both unknown-row counts

- **WHEN** the primary connector-registry query fails
- **THEN** the response SHALL set `connector_registry_available` to `false`,
  return an empty connector list, and report zero for both the existing
  unclassified count and the awaiting-first-heartbeat subset count
- **AND** the UI SHALL render the roster's unavailable state rather than an
  awaiting, unclassified, or empty-fleet claim

#### Scenario: Older responses retain their established interpretation

- **WHEN** a cached or older response omits `presentation_state` and the new
  awaiting subset count
- **THEN** the frontend SHALL preserve its existing `liveness` and
  `operational_role` interpretation
- **AND** an `unclassified` row SHALL remain unclassified rather than being
  inferred as awaiting from other response fields
- **AND** the absent subset count SHALL NOT fabricate an awaiting row or alter
  any fleet KPI

#### Scenario: Presentation exposes no evidence content

- **WHEN** an awaiting row is returned or rendered
- **THEN** the additive API and UI SHALL reveal only the derived presentation
  state and aggregate count
- **AND** they SHALL NOT add settings, scope, cursor, metadata-cache,
  credential, matched-predicate, or evidence-reason content to the roster,
  attention strip, telemetry, logs, or action URL

### Requirement: Awaiting-first-heartbeat verification seams are explicit

Implementation SHALL keep the derivation and its verification at the existing
runtime-authority seams:

- primary API derivation and counts in
  `src/butlers/api/routers/ingestion_connectors.py`;
- legacy API model, list/detail projection, and summary counts in
  `roster/switchboard/api/models.py` and `roster/switchboard/api/router.py`;
- wire types in `frontend/src/api/types.ts`;
- shared verdict derivation in
  `frontend/src/components/ingestion/connectors/connector-auth.ts`, row copy and
  optional action in `ConnectorRosterRow.tsx`, and roster count/catalog
  degradation in `ConnectorsRoster.tsx`;
- API contract coverage in `tests/api/test_connector_operational_role.py`;
- real-Postgres producer-ordering and non-demotion coverage in
  `tests/config/test_switchboard_connector_operational_role_migration.py`; and
- UI state, copy, action, degradation, and older-response coverage in
  `frontend/src/components/ingestion/connectors/ConnectorsRosterRuntimeAuthority.test.tsx`.

#### Scenario: Verification covers authority and presentation independently

- **WHEN** this specification is implemented
- **THEN** API tests SHALL exercise every fixed positive predicate, the
  unexplained-unknown fallback, checkpoint exclusion, subset counts, both
  degraded sources, and older-response compatibility
- **AND** real-Postgres tests SHALL exercise heartbeat promotion plus settings,
  observed-scope, metadata-cache, and self-owned-cursor writes in both
  before-heartbeat and after-heartbeat order, including a concurrent conflict
  case
- **AND** frontend tests SHALL assert the exact verdict, supporting copy,
  catalog-backed **“Review setup”** action, unsupported-action absence,
  content blindness, and unchanged fleet KPIs
- **AND** no test SHALL treat configuration, OAuth metadata, a cache, or a
  cursor as proof of runtime health

## Source References

- Non-Negotiable Rule 7 (transport is a connector responsibility) —
  `about/heart-and-soul/vision.md`
- RFC 0003 (Switchboard routing and ingestion)
- Degraded-envelope conventions —
  `docs/api_and_protocols/response-conventions.md`
- Roster and fleet-health contract —
  `openspec/specs/dashboard-ingestion-dispatch-console/spec.md`
- Tracked implementation bead — `bu-6jv4m.11`
- Awaiting-first-heartbeat specification bead — `bu-poven`
