## REMOVED Requirements

### Requirement: Dashboard Connector Page

**Reason**: The old `/connectors` card, volume-chart, and fanout contract was
retired when the first-class ingestion Dispatch surface replaced it. Retaining
it would require obsolete Switchboard connector endpoints and contradict the
canonical role-aware roster.

**Migration**: Use `/ingestion/connectors` for the roster and connector detail,
and use the `/api/ingestion/connectors` namespace for all dashboard connector
data. The retired fanout matrix has no replacement by owner decision.

## MODIFIED Requirements

### Requirement: Connector Settings API

Runtime-configurable connector settings SHALL be stored in
`connector_registry.settings` (JSONB) and managed through the canonical
dashboard endpoint
`PATCH /api/ingestion/connectors/{connector_type}/{endpoint_identity}/settings`.

#### Scenario: Settings storage

- **WHEN** a connector has runtime-configurable settings
- **THEN** they are stored in the `settings` JSONB column of
  `connector_registry`
- **AND** NULL means no settings overrides; non-NULL holds a JSON object
- **AND** settings are shallow-merged on update (top-level keys replaced, not
  deep-merged)

#### Scenario: Settings update API

- **WHEN** a PATCH request is sent to
  `/api/ingestion/connectors/{connector_type}/{endpoint_identity}/settings`
- **THEN** the body `{"settings": {...}}` is shallow-merged into the existing
  settings
- **AND** the updated `ConnectorEntry` is returned
- **AND** settings take effect on next connector restart (same semantics as
  cursor updates)

#### Scenario: Discretion settings schema

- **WHEN** a connector uses the shared discretion layer
- **THEN** its `settings.discretion` object may contain: `weight_bypass` (float,
  default 1.0), `weight_fail_open` (float, default 0.5)
- **AND** these thresholds are editable from the connector detail page in the
  dashboard
