## ADDED Requirements

### Requirement: Account-Security Carve-Out

The global-scope policy evaluator SHALL resolve a `skip` match to `metadata_only` when the envelope is a classified first-party account-security alert, and rule promotion SHALL NOT propose `skip` or `metadata_only` for an allowlisted account-security sender. Connector-scope `block` rules are unaffected.

The exact-address first-party carve-out SHALL remain a deterministic retention decision, not a route, goal admission or grant. Connector-scope `block` SHALL retain precedence; the carve-out SHALL NOT force blocked mail to ingest or publish an event. Neither the security label nor provider authentication SHALL establish owner origin, clear exposure or enlarge a ceiling. Any later model judgment SHALL require an admitted goal under RFC 0038 D1/D2/D5/D6/D10/D18; this policy exception SHALL NOT create an implicit consumer or subscription or waive the admitted goal's authority, approval or budget checks.

ID: REQ-ingestion-policy-005
Source: account-security-perception/proposal.md; account-security-perception/design.md; RFC 0038 D1/D2/D5/D6/D10/D18
Scope: v1-mandatory

#### Scenario: A promoted skip is demoted for an alert

- **WHEN** a promoted `skip` rule matches an allowlisted sender whose subject classifies as an alert
- **THEN** the global evaluator returns `metadata_only`

#### Scenario: Other mail from the sender is still skipped

- **WHEN** the same rule matches mail from that sender whose subject does not classify
- **THEN** the evaluator returns `skip`

#### Scenario: Promotion refuses an allowlisted sender

- **WHEN** rule promotion builds a proposed action of `skip` for an allowlisted account-security sender
- **THEN** no proposed action is produced
