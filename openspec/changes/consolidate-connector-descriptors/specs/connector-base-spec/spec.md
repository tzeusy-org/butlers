## ADDED Requirements

### Requirement: Static connector identity projections

The framework SHALL keep canonical connector identity and its S1 surface projections
in one frozen, pure-data catalog. The catalog SHALL contain no credential values,
read no environment or database, and SHALL NOT confer registration or authentication
authority through an alias or deployment flag.

ID: REQ-connector-base-spec-009
Source: bu-dss7he.9 released S1 and folded connector-family/config-and-deploy moves
Scope: v1-mandatory

#### Scenario: Distinct namespaces remain explicit

- **WHEN** connector identities are projected for a consumer
- **THEN** canonical types SHALL preserve the existing thirteen heartbeat identities
- **AND** preview display aliases, local/Compose deployability and Helm support SHALL be explicit projections rather than raw string-equality assumptions
- **AND** the exact existing nineteen accepted ingress pairs, including nonconnector ingress and supported secondary emissions, SHALL remain unchanged
- **AND** retired Discord and unknown connector types SHALL gain no registration or ingress authority

#### Scenario: Closed preview labels preserve safety

- **WHEN** the buffered-preview scrubber needs a fallback label
- **THEN** its closed label set SHALL derive from canonical descriptors and the existing explicit display aliases
- **AND** all sixteen existing safe labels SHALL remain supported without copying caller-controlled unknown labels into placeholders
- **AND** code/link detection, safe-label validation, idempotency and detector-error fail-withhold behavior SHALL remain unchanged

#### Scenario: New duplicate identity mechanisms are rejected

- **WHEN** a new hardcoded connector-type collection contains six or more current connector identities
- **THEN** a source contract check SHALL reject that duplicate mechanism
- **AND** temporary later-slice consumers and wire vocabularies SHALL be bound to their exact existing owner and literal population rather than exempting a whole file

## MODIFIED Requirements

### Requirement: Available connector discovery catalog

`GET /api/ingestion/connectors/available` SHALL return the connector types the
framework can deploy independently of `connector_registry` rows.  Each profile
is a deployability descriptor, not an orchestration-capability advertisement. The catalog SHALL derive its profiles
from the framework's frozen connector descriptors, using the actual canonical
channel/provider identity emitted by each deployable connector. Local/Compose
connectors SHALL remain discoverable even when they have no Helm deployment.

ID: REQ-connector-base-spec-010
Source: bu-dss7he.9 released S1; existing connector emitter identities and discovery contract
Scope: v1-mandatory

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
