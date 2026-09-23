## ADDED Requirements

### Requirement: [TARGET-STATE] Route eligibility uses separated control-plane facts
Switchboard SHALL derive target eligibility from receiver-observed current-generation health, administrative policy, and route compatibility, including staffer targets reachable through sanctioned butler-to-staffer routing. The old single `eligibility_state` and daemon-authored heartbeat SHALL not independently confer route authority.

ID: REQ-butler-switchboard-002
Source: heart-and-soul/vision.md Rule 3; RFC 0003 §Route Inbox and Crash Recovery; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.1
Scope: v1-mandatory

#### Scenario: Staffer remains reachable when policy and observation permit
- **WHEN** a butler routes a sanctioned tool call to a healthy compatible staffer with active administrative policy
- **THEN** Switchboard permits the call without adding the staffer to user-message classification candidates

#### Scenario: Staleness has one bounded recovery opportunity
- **WHEN** a target is stale and otherwise eligible
- **THEN** Switchboard applies the bounded on-demand probe contract before refusing the route
- **AND** a failure returns a typed transport outcome without an attempted target effect

#### Scenario: Old registry writes cannot clear owner policy
- **WHEN** startup registration, route bookkeeping, or a legacy heartbeat attempts to replace a quarantined target's registry row
- **THEN** the stored owner policy remains quarantined and the route remains ineligible

### Requirement: [TARGET-STATE] Effect-free control-plane route preflight

Switchboard SHALL own an internal, read-only
`GET /internal/control-plane/route-preflight` operation on its existing backend
port. The separately supervised Dashboard controller MAY sample it; public
`GET /ready` SHALL consume only the controller's bounded cached result and
SHALL NOT invoke this operation on each public request. The operation takes
no caller-selected target or endpoint. Switchboard SHALL choose the lexically
first configured non-paused domain target from the exact Git roster, traverse
the same pure route-selection, administrative-policy, registry-eligibility,
compatibility, and endpoint-resolution logic used by production routing, then
perform one bounded identity GET through the shared current-epoch verifier.
The result SHALL be a fixed, content-blind ready boolean and safe internal
failure category; no target name, address, identity, or raw error may enter
the public readiness response. Switchboard SHALL coalesce concurrent
preflight requests and enforce a server-owned minimum interval with a cached
result so an internal caller cannot turn this read into unbounded identity
traffic; stale cache without an allowed refresh SHALL fail closed.

The preflight SHALL NOT call any target MCP tool or `route.execute`, claim an
intent, reserve or record a liveness probe sequence, update registry state,
or write a routing log, target inbox, session, notification, ingestion, or
other durable evidence row. Its identity GET is a read, not target acceptance.
Missing fixed target, inaccessible Switchboard, DB or policy lookup failure,
stale or denied policy, incompatible contract, wrong epoch or identity,
timeout, and malformed response SHALL fail closed. This internal endpoint is
available only on the adopted trusted backend network and grants no owner
credential or public routing authority; split-host exposure requires a
separate trust amendment.

ID: REQ-butler-switchboard-004
Source: RFC 0003 Receiver-Derived Routing Eligibility; RFC 0007 Amendment 3; docs/reviews/2026-09-23-reliability-beads-plan.md Q4
Scope: v1-mandatory

#### Scenario: Fixed-target preflight uses the production selection path
- **WHEN** the Dashboard controller samples Switchboard while a configured non-paused domain target is current, compatible, and accepting
- **THEN** Switchboard selects that fixed target through the production policy and endpoint resolver and returns a bounded content-blind ready result after one verified identity GET
- **AND** it makes no target tool call or durable evidence write

#### Scenario: Failure is not a healthy canary
- **WHEN** there is no fixed target, a policy or DB read fails, the target is denied or stale, or the identity GET is malformed, mismatched, or times out
- **THEN** the internal result is not ready and the public cached `route_canary` check is false or unavailable
- **AND** neither an old healthy observation nor a failed read is treated as target acceptance

#### Scenario: Public readiness does not execute the preflight
- **WHEN** an unauthenticated client polls exact `GET /ready`
- **THEN** the response uses the bounded controller snapshot without causing a Switchboard preflight, target call, database write, or new network fanout
- **AND** it exposes only the fixed public check booleans

#### Scenario: Repeated internal reads are bounded
- **WHEN** concurrent or rapid internal clients request the preflight
- **THEN** Switchboard coalesces them and performs at most one bounded identity GET per server-owned interval
- **AND** a stale or unavailable cached result cannot be promoted to ready merely to avoid another probe
