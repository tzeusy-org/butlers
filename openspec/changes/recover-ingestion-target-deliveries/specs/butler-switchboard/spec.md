## ADDED Requirements

### Requirement: Durable ordinary ingestion target handoff

The Switchboard SHALL dispatch non-dashboard ingestion-to-domain `route.execute` only from a committed target-delivery intent and SHALL pass its stable delivery identity to the target's atomic acceptance boundary. It SHALL preserve the existing contracts for dashboard routing, Messenger notification delivery, domain-event subscribers, explicit misroute correction, and connector ingress replay.

ID: REQ-butler-switchboard-003
Source: heart-and-soul/vision.md Rule 3; RFC 0003 §route.execute Envelope; design.md Decision 2
Scope: v1-mandatory

#### Scenario: Ordinary route carries durable identity

- **WHEN** Switchboard routes a classified non-dashboard ingestion segment to a domain butler
- **THEN** the call SHALL carry the committed event, target, and segment delivery identity
- **AND** a confirmed result SHALL be associated with the target's stable acceptance receipt

#### Scenario: Other routing lanes keep their own contracts

- **WHEN** a dashboard turn, Messenger delivery, domain-event subscriber call, or explicit misroute correction routes through Switchboard
- **THEN** its existing admission, acknowledgement, retry, and authorization policy SHALL remain authoritative
- **AND** the ordinary ingestion delivery worker SHALL NOT claim or replay that work

#### Scenario: Target policy is checked on each attempt

- **WHEN** a committed intent becomes due after its target's route policy or eligibility changed
- **THEN** Switchboard SHALL re-evaluate current route authority before transport
- **AND** policy refusal SHALL be recorded as terminal rather than silently converted to a transient retry
