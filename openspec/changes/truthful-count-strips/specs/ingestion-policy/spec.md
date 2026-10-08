## ADDED Requirements

### Requirement: Classification publication truth survives strengthened heartbeat recording

The strengthened paired heartbeat commit SHALL preserve current exact-request classification admission and local contact-query truth. An admitted ACK SHALL remain after committed registry admission; a refused request SHALL mutate neither registry nor history. Required history failure SHALL now roll back that admitted registry transaction and produce publication failure rather than a precommit accepted ACK. Successful local queries and genuine drop-time history SHALL retain their loaded state and old generation/TTL/provider-policy behavior even when publication fails. The heartbeat recording history SHALL NOT become classification authority.

ID: REQ-ingestion-policy-003
Source: bu-s11n0s.5 complete-protocol D1-D6; heart-and-soul/vision.md failure and staleness honesty; owner-timezone-context and dashboard-design-language temporal contracts
Scope: v1-mandatory

#### Scenario: Loaded query remains loaded after append failure

- **WHEN** a local successful contact query is followed by required heartbeat append failure
- **THEN** the local query and genuine drop-time classification SHALL remain loaded
- **AND** no failed registry transaction SHALL produce an adoptable admitted ACK
- **AND** overall API admission availability SHALL retain its independent freshness checks

#### Scenario: Refusal remains mutation free

- **WHEN** the exact current request fails existing classification admission
- **THEN** the existing bounded refusal ACK SHALL remain valid for that request only
- **AND** no history append, registry recency renewal or coverage activation SHALL occur

