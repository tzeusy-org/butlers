# Expected Signals

## Purpose

Defines the shared deterministic primitive that prevents instrument failure
from becoming a fabricated claim about owner behavior.

## Requirements

### Requirement: Shared producer-owned expected-signal ledger

`public.expected_signals` SHALL store one row per `signal_key`, including its
producer, producer runtime role, positive expected cadence, last observation,
evaluation time, and `measurability` value `present|absent|unmeasurable`. Runtime roles SHALL be
able to idempotently insert or update their own keys and SHALL NOT update keys
owned by another runtime role.

#### Scenario: Concurrent keyed upserts converge

- **WHEN** one producer concurrently evaluates the same signal key
- **THEN** exactly one row SHALL remain
- **AND** the row SHALL carry one complete evaluation rather than a partial merge

#### Scenario: Another producer cannot overwrite the key

- **WHEN** a different runtime role attempts to update an existing signal key
- **THEN** row-level policy SHALL refuse the write

#### Scenario: Standard backup remains publishable

- **WHEN** the standard non-privileged `pg_dump` backup runs
- **THEN** it SHALL explicitly exclude the forced-RLS expected-signal projection
- **AND** it SHALL NOT enable row security or abort the whole backup
- **AND** source observations and connector liveness SHALL remain backed up so the next detector run can rebuild the projection

### Requirement: Liveness precedes absence

An elapsed cadence SHALL be `absent` only while its producer is measurable.
Connector producers SHALL use the canonical heartbeat projection and five-minute
liveness window. Missing, unhealthy, stale, offline, or unreadable producer
evidence SHALL be `unmeasurable`, never `absent`.

#### Scenario: Live elapsed signal is absent

- **WHEN** a healthy connector heartbeat is current and evaluation is at or after the exact expected-cadence boundary
- **THEN** the signal SHALL be `absent`

#### Scenario: Dead instrument makes the gap unmeasurable

- **WHEN** connector liveness is killed or stale and the same cadence elapses
- **THEN** the signal SHALL be `unmeasurable`
- **AND** no owner-behavior gap candidate SHALL be emitted

#### Scenario: Mixed connector provenance has no guessed authority

- **WHEN** one signal history contains observations from both Google Health and Home Assistant
- **THEN** producer resolution SHALL be order-independent and return unknown
- **AND** the signal SHALL be `unmeasurable` unless a stronger authoritative mapping is defined

### Requirement: Expected-signal reads degrade honestly

Expected-signal APIs and clients SHALL distinguish an unavailable ledger from a
complete empty result. Clients SHALL render `unmeasurable` as instrument failure,
not as owner absence.

#### Scenario: API read failure is not an all-clear

- **WHEN** the expected-signal query fails
- **THEN** the API SHALL return `available=false` and `signals=null`
- **AND** the client SHALL state that signal health is unavailable and nudges are paused

### Requirement: Connector expected signals bind exact endpoint identity

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
