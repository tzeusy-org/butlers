# Connector Base Spec — Available Connector Catalog Delta

This delta defines the discovery-only response contract for
`GET /api/ingestion/connectors/available`.  It does not modify the separate
Switchboard MCP backfill protocol or connector heartbeat capabilities.

## ADDED Requirements

### Requirement: Available connector discovery catalog

`GET /api/ingestion/connectors/available` SHALL return the connector types the
framework can deploy independently of `connector_registry` rows.  Each profile
is a deployability descriptor, not an orchestration-capability advertisement.

#### Scenario: Profile has the exact discovery shape

- **WHEN** a client successfully requests
  `GET /api/ingestion/connectors/available`
- **THEN** every item in the response `data` array SHALL contain exactly
  `connector_type`, `channel`, `provider`, and `display_name`
- **AND** every item SHALL contain no additional fields
- **AND** the response model and dashboard API type SHALL represent that same
  four-field shape

#### Scenario: Catalog does not depend on deployed instances

- **WHEN** no connector instance is registered in `connector_registry`
- **THEN** `GET /api/ingestion/connectors/available` SHALL still return the
  framework's deployable connector catalog
- **AND** the request SHALL not require a connector-registry database read

#### Scenario: Backfill orchestration remains an internal protocol

- **WHEN** a connector participates in backfill orchestration
- **THEN** it SHALL use the Switchboard-owned `backfill.poll` and
  `backfill.progress` MCP tools defined by the connector protocol
- **AND** the available-connector discovery response SHALL not advertise
  backfill support or infer backfill behavior for any connector type

## Source References

- Non-Negotiable Rule 7 (transport is connector responsibility) —
  `about/heart-and-soul/vision.md`
- RFC 0002 (MCP Tool Surface and Modules) — Switchboard-only
  `backfill.poll` and `backfill.progress` tools
- Connector base capability —
  `openspec/specs/connector-base-spec/spec.md`
