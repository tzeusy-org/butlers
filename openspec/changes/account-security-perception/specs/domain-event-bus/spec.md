## ADDED Requirements

### Requirement: Security Event Contract

The Switchboard SHALL declare `switchboard.security_event` in its publisher-owned contract with the required fields `kind`, `provider`, `sender_verification` and `source_request_id`, and no permitted subscribers until a consumer is declared.

The fixed contract and deterministic publication SHALL NOT implicitly create a consumer, subscription, grant or reasoning invocation. Any future declared consumer performing model judgment SHALL use goal admission under RFC 0038 D1/D2/D5/D6/D10/D18, with server-held origin/cause, applicable event assertion lineage, inherited exposure, bounded origin ceiling and finite budget. Effect-caused admissions SHALL intersect the declared subscription set with the causing goal's origin ceiling and enforce the configured causal-chain limit. Domain-published input without goal lineage SHALL be exposed unless stamped as derived only from untainted owner-authority records; missing or unreadable stamps SHALL fail closed as exposed. A security label or provider-authentication verdict SHALL NOT authenticate an owner, upgrade origin, clear exposure or enlarge authority. The fixed payload's required and optional fields SHALL remain unchanged; admission lineage SHALL use the adopted runtime context, not new security-event payload fields.

ID: REQ-domain-event-bus-001
Source: account-security-perception/proposal.md; account-security-perception/design.md; RFC 0038 D1/D2/D5/D6/D10/D18
Scope: v1-mandatory

#### Scenario: The live payload is admitted

- **WHEN** the Switchboard publishes a security event with the required and optional declared fields
- **THEN** the contract registry admits the publish
