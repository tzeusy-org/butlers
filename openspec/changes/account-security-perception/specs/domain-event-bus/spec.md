## ADDED Requirements

### Requirement: Security Event Contract

The Switchboard SHALL declare `switchboard.security_event` in its publisher-owned contract with the required fields `kind`, `provider`, `sender_verification` and `source_request_id`, and no permitted subscribers until a consumer is declared.

#### Scenario: The live payload is admitted

- **WHEN** the Switchboard publishes a security event with the required and optional declared fields
- **THEN** the contract registry admits the publish
