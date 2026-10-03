## ADDED Requirements

### Requirement: Account-Security Carve-Out

The global-scope policy evaluator SHALL resolve a `skip` match to `metadata_only` when the envelope is a classified first-party account-security alert, and rule promotion SHALL NOT propose `skip` or `metadata_only` for an allowlisted account-security sender. Connector-scope `block` rules are unaffected.

#### Scenario: A promoted skip is demoted for an alert

- **WHEN** a promoted `skip` rule matches an allowlisted sender whose subject classifies as an alert
- **THEN** the global evaluator returns `metadata_only`

#### Scenario: Other mail from the sender is still skipped

- **WHEN** the same rule matches mail from that sender whose subject does not classify
- **THEN** the evaluator returns `skip`

#### Scenario: Promotion refuses an allowlisted sender

- **WHEN** rule promotion builds a proposed action of `skip` for an allowlisted account-security sender
- **THEN** no proposed action is produced
