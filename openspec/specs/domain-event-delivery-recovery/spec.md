# Domain Event Delivery Recovery

## Purpose

Defines owner visibility and explicit replay for terminal `failed_permanent` rows in `public.domain_event_deliveries`. Replay reuses the existing durable delivery and domain-event dispatch machinery; it does not introduce a second event bus or infer that delivery succeeded.

## Requirements

### Requirement: Permanently failed deliveries remain discoverable

The existing domain-event delivery API and butler console SHALL expose `failed_permanent` deliveries as terminal failures with their bounded delivery metadata.

#### Scenario: Failed delivery is visible on the owning butler
- **WHEN** a butler has a recent domain-event delivery in `failed_permanent`
- **THEN** its domain-events panel SHALL render the delivery with a failure treatment
- **AND** it SHALL offer an explicit Replay verb

#### Scenario: Failed-delivery read is unavailable
- **WHEN** the delivery API cannot provide the requested rows
- **THEN** the panel SHALL preserve its named unavailable state
- **AND** it SHALL not render an empty success state

### Requirement: Failed delivery replay is atomic and idempotent

The dashboard API SHALL expose `POST /api/domain-events/deliveries/{delivery_id}/replay`. The verb SHALL make the existing durable row eligible for the ordinary delivery worker again; it SHALL not directly invoke a destination tool from the API process.

#### Scenario: First replay resets the terminal delivery
- **WHEN** the owner replays a valid `failed_permanent` delivery
- **THEN** one atomic transition SHALL set it to `pending`
- **AND** stale attempt, error, task, and delivery-result fields SHALL be cleared so the normal dispatcher owns the next attempt
- **AND** the response SHALL identify the delivery and report `status="pending"`

#### Scenario: Repeated or concurrent replay does not duplicate work
- **WHEN** a second or concurrent replay targets a delivery that is no longer `failed_permanent`
- **THEN** it SHALL return HTTP 409 Conflict
- **AND** it SHALL not perform another transition or enqueue another delivery attempt

#### Scenario: Unknown or malformed delivery id fails visibly
- **WHEN** the replay target is malformed or does not exist
- **THEN** the API SHALL return the corresponding validation or not-found error
- **AND** no delivery row SHALL be changed
