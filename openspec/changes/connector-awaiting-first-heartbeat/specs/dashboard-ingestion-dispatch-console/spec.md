## ADDED Requirements

### Requirement: Awaiting first heartbeat is an additive unknown-row presentation

The connector APIs SHALL preserve `liveness: "unclassified"` and every existing
unclassified count for all `operational_role = 'unknown'` rows. Within that
umbrella, they SHALL expose the content-blind presentation derived by
`connector-base-spec` as an additive nullable field:

ID: REQ-dashboard-ingestion-dispatch-console-009
Source: Owner-adopted PR #4046 at 1a14d530 (2026-09-15), bu-poven; connector-runtime-instance-authority presentation amendment
Scope: v1-mandatory

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

ID: REQ-dashboard-ingestion-dispatch-console-010
Source: Owner-adopted PR #4046 at 1a14d530 (2026-09-15), bu-poven; connector-runtime-instance-authority presentation amendment
Scope: v1-mandatory

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

- Owner-adopted PR #4046 at `1a14d530abad009ad5ea08c5d71be3887c5a094a`; bu-poven, 2026-09-15 decision.
- Runtime authority baseline: `openspec/specs/connector-base-spec/spec.md`.
- Roster contract: `openspec/specs/dashboard-ingestion-dispatch-console/spec.md`.
