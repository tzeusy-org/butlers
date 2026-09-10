## REMOVED Requirements

### Requirement: Connector Settings API

**Reason**: Its route and universal restart wording belonged to the retired
Switchboard connector API family and contradicted the supported batch-settings
live reload behavior.

**Migration**: Use `Canonical Connector Settings API`, which owns the
`/api/ingestion/connectors/{type}/{identity}/settings` route and explicit
per-setting reload boundaries.

## ADDED Requirements

### Requirement: Canonical Connector Settings API

Runtime-configurable connector settings SHALL be stored in
`connector_registry.settings` (JSONB) and managed through
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
- **AND** the updated `ConnectorDetail` is returned
- **AND** each setting takes effect at its documented connector reload boundary
- **AND** `flush_interval_s` takes effect on the next flush scanner cycle
  without a connector restart

#### Scenario: Discretion settings schema

- **WHEN** a connector uses the shared discretion layer
- **THEN** its `settings.discretion` object may contain: `weight_bypass` (float,
  default 1.0), `weight_fail_open` (float, default 0.5)
- **AND** these thresholds are editable from the connector detail page in the
  dashboard
