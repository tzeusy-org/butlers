## ADDED Requirements

### Requirement: Static connector identity projections

The framework SHALL keep canonical connector identity and its static consumer projections
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

#### Scenario: Consumer projections preserve policy and stored history

- **WHEN** ingress, heartbeat, policy or conversation-history consumers read catalog projections
- **THEN** the existing nineteen wire pairs, thirteen heartbeat identities, thirteen policy selectors and twelve stored-history strategies SHALL remain unchanged
- **AND** each API validator, unknown-selector refusal and audit-attribution rule SHALL retain its authority
- **AND** Discord history SHALL remain readable without granting registration or ingress authority

#### Scenario: Scope and auth projections preserve authority boundaries

- **WHEN** the API adapts catalog metadata into its existing auth and scope response types
- **THEN** all five ordered manifests, versions, scope categories, notes and approval reasons SHALL remain unchanged
- **AND** six Google scope groups, default ordering, TOML precedence and explicit Health consent SHALL remain unchanged
- **AND** provider and channel selectors SHALL preserve every shared or unsupported alternative without inferring a canonical connector or auth flow
- **AND** canonical and legacy WhatsApp SHALL use managed-session classification and the existing Passport pairing route without exposing session material
- **AND** ActivityWatch SHALL identify local environment configuration without claiming remote enrollment or observed credential validity
- **AND** retired or unknown selectors SHALL remain unsupported

#### Scenario: Public recovery data stays closed and reproducible

- **WHEN** the small public recovery projection is generated or checked
- **THEN** deterministic generation SHALL contain only closed recovery capabilities and explicit Health opt-in metadata
- **AND** the existing recovery consumer SHALL preserve literal provider, origin, return-context and consent behavior
- **AND** stale generated output SHALL fail its check and prototype/unknown selectors SHALL yield no OAuth or Passport destination
- **AND** API DTO generation, connector-owned Spotify consent and runtime health/status algorithms SHALL remain independent

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

### Requirement: Environment Variables (Base)
All connectors SHALL derive static connector type, channel and provider from their canonical descriptor. Transport and operational parameters SHALL remain environment-configurable.

ID: REQ-connector-base-spec-011
Source: bu-dss7he.9 released S3; openspec/specs/connector-base-spec/spec.md requirement Environment Variables (Base)
Scope: v1-mandatory

#### Scenario: Required base environment variables
- **WHEN** a connector starts
- **THEN** `SWITCHBOARD_MCP_URL` (Switchboard SSE endpoint) must be set; channel and provider SHALL come from the canonical descriptor and SHALL NOT be overridden by `CONNECTOR_PROVIDER` or `CONNECTOR_CHANNEL`
- **AND** each connector auto-resolves its `endpoint_identity` at startup (e.g., by calling the provider's "get me" API) rather than requiring it as an env var
- **AND** checkpoint persistence is DB-backed via `cursor_store` (no file path env var needed)

#### Scenario: Optional environment variables
- **WHEN** a connector starts
- **THEN** `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT`, `CONNECTOR_POLL_INTERVAL_S`, `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120), and `CONNECTOR_HEARTBEAT_ENABLED` (default true) are optionally configurable

---
