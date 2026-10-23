## ADDED Requirements

### Requirement: Memory details display server-stamped authorship

ID: REQ-dashboard-domain-pages-060
Source: bu-q7vx1q.26 approved dashboard outcome; REQ-module-memory-013
Scope: v1-mandatory

FactDetailPage and RuleDetailPage SHALL display content authorship from persisted server-derived authority, independently of the record's subject, request ID, and source episode availability. The existing episode provenance links, unavailable-source text, lifecycle controls, and error boundaries SHALL remain intact.

#### Scenario: Detail identifies the content author honestly

- **WHEN** a fact or rule detail has owner, owner-device, third-party, system, mixed, or NULL content authority
- **THEN** the page SHALL distinguish those classes and link an identified author to its entity page using its canonical name when resolved
- **AND** it SHALL not infer a physical device name, scheduled execution, known record age, or owner authority from missing stamps or request metadata
- **AND** unavailable episode sources SHALL remain non-links with their existing availability state

#### Scenario: Endorsement and held guidance retain their actual meaning

- **WHEN** a rule has a recorded endorsement
- **THEN** its detail SHALL show the recorded endorser and date, keeping unresolved identity explicit
- **WHEN** a live unendorsed rule is held
- **THEN** its waiting link SHALL open the actual rules register filtered by held=true
- **AND** maturity and pagination SHALL preserve that filter, with a visible way to clear it
- **AND** retired, forgotten, or endorsed rules SHALL not be presented as guidance waiting for the owner

## Source References

- Non-Negotiable Rule 1: user-federated ownership of data
- Non-Negotiable Rule 3: durable facts retain their owner and provenance
- REQ-module-memory-013: server-derived authority and held-rule admission
- bu-q7vx1q.26: approved read-only dashboard outcome
