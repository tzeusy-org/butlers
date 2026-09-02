## ADDED Requirements

### Requirement: [TARGET-STATE] Connector expected signals bind exact endpoint identity

A connector-backed expected signal MUST carry `producer_endpoint_identity` from server-derived
source provenance and MUST evaluate liveness against the exact `(connector_type,
endpoint_identity)` pair. Connector type alone MUST NOT authorize measurability when multiple
endpoints exist.

ID: REQ-expected-signals-001
Source: RFC 0029 §Decision; finance-recurrence-producer-mapping design §2
Scope: v1-mandatory

#### Scenario: Exact endpoint heartbeat authorizes measurability

- **WHEN** a connector-backed signal names a producer type and endpoint identity
- **THEN** liveness MUST be read for that exact type/endpoint pair
- **AND** only a healthy/current heartbeat for that pair may authorize `present` or `absent`

#### Scenario: Healthy sibling endpoint never substitutes

- **WHEN** endpoint A is dead, stale, unhealthy, missing, or unreadable and endpoint B of the same
  connector type is healthy/current
- **THEN** endpoint A's signal MUST be `unmeasurable`
- **AND** this result MUST be invariant to liveness row order

#### Scenario: Connector endpoint identity is required

- **WHEN** a connector-backed expected signal lacks `producer_endpoint_identity`
- **THEN** it MUST be `unmeasurable`
- **AND** the evaluator MUST NOT fall back to any healthy endpoint of that connector type

#### Scenario: Owner source has no connector endpoint

- **WHEN** an expected signal has the server-attested `owner` producer
- **THEN** `producer_endpoint_identity` MUST be absent
- **AND** connector liveness MUST NOT be queried
