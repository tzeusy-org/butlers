# connector-base-spec

## MODIFIED Requirements

### Requirement: Connector Liveness and Eligibility

The Switchboard SHALL derive connector liveness exclusively from
`last_heartbeat_at` through `derive_liveness` and manage eligibility state
transitions separately from the connector's stored operational health state.
The stored `state` value (`healthy`, `degraded`, or `error`) SHALL remain
source-health evidence and SHALL NOT override heartbeat-derived liveness.

#### Scenario: Liveness thresholds
- **WHEN** a connector's liveness is evaluated
- **THEN** `derive_liveness(last_heartbeat_at)` reports `online` when last
  heartbeat age is under 5 minutes, `stale` when it is 5-15 minutes, and
  `offline` when it is over 15 minutes or no heartbeat was ever received
- **AND** a heartbeat more than the permitted future clock-skew tolerance
  ahead of server time is `offline`, never a false-healthy result

#### Scenario: Stored health state does not override liveness
- **WHEN** a connector has a recent heartbeat with `state = error`
- **THEN** its liveness is `online` and its independent state remains `error`
- **WHEN** a connector has a stale or offline heartbeat with `state = healthy`
- **THEN** its liveness remains `stale` or `offline` from heartbeat recency
  and is not presented as live because of the stored state

#### Scenario: Eligibility states
- **WHEN** a connector's eligibility is evaluated
- **THEN** it is one of: `active` (heartbeat within liveness TTL), `stale`
  (no heartbeat within TTL), `quarantined` (explicitly flagged), or an
  explicit operator-paused exclusion where supported
- **AND** quarantine and paused operator suppression take precedence over
  ordinary eligibility use but do not manufacture heartbeat liveness

#### Scenario: Eligibility transition auditing
- **WHEN** a connector's eligibility state changes
- **THEN** an audit log entry is written with: connector name, previous state,
  new state, reason, timestamps

#### Scenario: No automatic deregistration
- **WHEN** a connector goes offline
- **THEN** the record persists in `connector_registry` for historical
  visibility — cleanup is operator-only

### Requirement: Pydantic Response Models

The system SHALL define explicit Pydantic response models for the connector
dashboard API. The wire contract SHALL be owned by the canonical
`/api/ingestion/connectors` routes; frontend display models MAY project that
wire data but SHALL NOT be presented as a second backend response contract.
Liveness fields in connector summaries SHALL be derived from
`last_heartbeat_at`, while stored state retains independent operational-health
meaning.

#### Scenario: ConnectorSummary model
- **WHEN** a connector list response is serialized
- **THEN** each entry includes: `connector_type`, `endpoint_identity`,
  `liveness`, `state`, `error_message`, `version`, `uptime_s`,
  `last_heartbeat_at`, `first_seen_at`, and optional `today` summary
- **AND** `liveness` is the result of `derive_liveness(last_heartbeat_at)`
  rather than a projection of `state`

#### Scenario: Connector detail wire model
- **WHEN** `GET /api/ingestion/connectors/{type}/{identity}` serializes a
  connector detail response
- **THEN** it returns one flat detail record with registry identity/health
  fields, registration metadata, lifetime/today counters, checkpoint fields,
  settings, and the content-blind `auth` and `scopes` blocks
- **AND** `settings` is an optional JSONB dict containing runtime-configurable
  connector settings (e.g. discretion thresholds)
- **AND** no token, refresh credential, or secret is present in the response

#### Scenario: Connector statistics wire model
- **WHEN** `GET /api/ingestion/connectors/{type}/{identity}/stats` serializes
  a statistics response
- **THEN** it returns flat hourly or daily rows with connector identity, bucket,
  ingested/failed/filtered counts, and health counters
- **AND** the response metadata reports whether the durable history query was
  available instead of fabricating a quiet series

## Source References
- Non-Negotiable Rule 7 (connector transport responsibility)
- RFC 0001 (deterministic infrastructure lifecycle)
- `infrastructure-reliability` (heartbeat-derived liveness authority)
