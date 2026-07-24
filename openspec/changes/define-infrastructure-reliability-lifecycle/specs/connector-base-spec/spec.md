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

The system SHALL define core Pydantic response models for the connectors
dashboard and API endpoints. Their liveness fields SHALL be derived from
`last_heartbeat_at` and their state fields SHALL retain independent operational
health meaning.

#### Scenario: ConnectorSummary model
- **WHEN** a connector list response is serialized
- **THEN** each entry includes: `connector_type`, `endpoint_identity`,
  `liveness`, `state`, `error_message`, `version`, `uptime_s`,
  `last_heartbeat_at`, `first_seen_at`, and optional `today` summary
- **AND** `liveness` is the result of `derive_liveness(last_heartbeat_at)`
  rather than a projection of `state`

#### Scenario: ConnectorDetail model
- **WHEN** a connector detail response is serialized
- **THEN** it extends ConnectorSummary with: `instance_id`, `registered_via`,
  `checkpoint`, `counters`, `settings`
- **AND** `settings` is an optional JSONB dict containing runtime-configurable
  connector settings (e.g. discretion thresholds)

#### Scenario: ConnectorStats model
- **WHEN** a statistics response is serialized
- **THEN** it includes: `connector_type`, `endpoint_identity`, `period`,
  `summary`, `timeseries`

#### Scenario: ConnectorFanoutEntry model
- **WHEN** a fanout response is serialized
- **THEN** it includes: `connector_type`, `endpoint_identity`, `targets`
  (butler_name → message_count)

## Source References
- Non-Negotiable Rule 7 (connector transport responsibility)
- RFC 0001 (deterministic infrastructure lifecycle)
- `infrastructure-reliability` (heartbeat-derived liveness authority)
