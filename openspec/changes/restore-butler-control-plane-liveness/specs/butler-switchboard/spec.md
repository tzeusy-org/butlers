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
