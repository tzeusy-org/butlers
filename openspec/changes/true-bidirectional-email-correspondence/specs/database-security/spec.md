## ADDED Requirements

### Requirement: Read-only correspondence aggregate grant

Under the RFC 0010 exception pattern, `butler_relationship_rw` SHALL receive
`USAGE` on schema `messenger` and `SELECT` on `messenger.v_confirmed_email_outbound`
only. No role other than Messenger's SHALL have access to
`messenger.email_correspondence`. The grant SHALL be created by Messenger's
migration chain and revoked before the view is dropped on downgrade.

#### Scenario: Relationship cannot read the ledger

- **WHEN** `butler_relationship_rw` selects from `messenger.email_correspondence`
- **THEN** PostgreSQL denies the query

#### Scenario: Relationship can read the aggregate

- **WHEN** `butler_relationship_rw` selects from `messenger.v_confirmed_email_outbound`
- **THEN** the query succeeds and returns only the aggregate columns

#### Scenario: Other roles are denied

- **WHEN** any other butler, connector, or dashboard role selects from the view or table
- **THEN** PostgreSQL denies the query
