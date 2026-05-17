# Connector Base Spec — OAuth Scope Surface Delta

This delta extends `connector-base-spec` additively to expose the auth and
scope state needed by `connector-oauth-scope-surface/spec`. It modifies
neither the existing connector lifecycle nor the existing
`ConnectorSummary` / `ConnectorDetail` field shape; it only adds new fields
and new connector-side responsibilities for OAuth providers.

The fully detailed scope-surface behavior lives in
`connector-oauth-scope-surface/spec`. This delta provides the data-model
hooks (additive columns + additive Pydantic fields) that the parent spec
consumes.

## ADDED Requirements

### Requirement: Auth-state columns on connector_registry

The `public.connector_registry` table SHALL include four additive columns to
expose auth and scope state. All columns are nullable; no data backfill is
required.

#### Scenario: Additive columns present

- **WHEN** the `public.connector_registry` schema is the most recent
  migration head
- **THEN** the table SHALL include the columns:
  - `observed_scopes TEXT[] NULL`
  - `observed_scopes_fetched_at TIMESTAMPTZ NULL`
  - `required_scopes_version SMALLINT NULL`
  - `auth_status VARCHAR(32) NULL`
- **AND** existing columns SHALL be unchanged in type, default, or
  constraint
- **AND** existing inserts, queries, and rollups SHALL continue to function
  without modification

#### Scenario: NULL values are well-defined

- **WHEN** any of the four columns is NULL
- **THEN** the meaning SHALL be as specified in
  `connector-oauth-scope-surface/spec` (§Observed-scope storage on
  connector_registry §Scenario: NULL semantics on read)
- **AND** dashboard surfaces SHALL NOT treat NULL as a database error — NULL
  is the documented absence-of-observation state

### Requirement: ConnectorDetail Pydantic auth and scopes blocks

The `ConnectorDetail` Pydantic response model SHALL be extended additively to include an `auth` block and a `scopes` block, populated per `connector-oauth-scope-surface/spec` §Dashboard API response shape. The model is defined by `connector-base-spec`.

#### Scenario: ConnectorDetail includes auth block

- **WHEN** `GET /api/ingestion/connectors/{type}/{identity}` returns a
  `ConnectorDetail` payload
- **THEN** the payload SHALL include an `auth` field whose shape conforms to
  `connector-oauth-scope-surface/spec` §Dashboard API response shape
- **AND** the `auth.status` field SHALL be a non-null enum value drawn from
  `{ok, degraded, expired, rotation-needed, unsupported, unconfigured}`

#### Scenario: ConnectorDetail includes scopes block

- **WHEN** the same endpoint returns a `ConnectorDetail` payload
- **THEN** the payload SHALL include a `scopes` array
- **AND** for connectors with `auth.status = unsupported`, the array SHALL
  be the empty list `[]`
- **AND** for OAuth-bound connectors, the array entries SHALL conform to
  the per-scope shape in `connector-oauth-scope-surface/spec`

#### Scenario: Backward compatibility for ConnectorSummary

- **WHEN** the `ConnectorSummary` model (used for list endpoints, per
  `connector-base-spec/spec.md:384-387`) is serialized
- **THEN** the model SHALL NOT be extended with the `auth` or `scopes`
  blocks (those are detail-only to keep list-page payloads small)
- **AND** the list endpoint MAY include a single `auth_status` enum field on
  each summary entry for the connector-attention strip — but full scope
  state is reserved for the detail endpoint

### Requirement: OAuth connector observation responsibility

OAuth-bound connectors SHALL maintain `observed_scopes` and `observed_scopes_fetched_at` on their `connector_registry` row per the re-introspection cadence defined in `connector-oauth-scope-surface/spec` §Re-introspection cadence and triggers. The applicability matrix in `connector-oauth-scope-surface/spec` determines which connectors are OAuth-bound. This is an additive responsibility; it does not modify the existing heartbeat protocol.

#### Scenario: OAuth connector updates observed scopes

- **WHEN** an OAuth-bound connector successfully refreshes its access token
  OR completes a fallback-cadence introspection
- **THEN** the connector SHALL update its row's `observed_scopes` and
  `observed_scopes_fetched_at` per the contract in
  `connector-oauth-scope-surface/spec`
- **AND** the connector SHALL recompute and persist `auth_status` per the
  parent spec's §Auth status computation requirement

#### Scenario: Non-OAuth connectors do not observe scopes

- **WHEN** a non-OAuth connector (Telegram bot, OwnTracks, etc.) starts
- **THEN** it SHALL NOT attempt to populate `observed_scopes` (the column
  remains NULL)
- **AND** the dashboard API SHALL compute `auth_status = unsupported` for
  the row based on the per-connector applicability matrix in
  `connector-oauth-scope-surface/spec`

## Source References

- Parent capability — `connector-oauth-scope-surface/spec` (this change)
- Connector base spec being extended —
  `openspec/specs/connector-base-spec/spec.md:319-348,381-419`
- Non-Negotiable Rule 7 (transport is connector responsibility) —
  `about/heart-and-soul/vision.md:110-115`
- Connector-detail endpoint owner —
  `openspec/specs/connector-base-spec/spec.md:388-392`
