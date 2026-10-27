## ADDED Requirements

### Requirement: Provider Allowance State Write Grants
`public.provider_allowance_states` SHALL be readable and writable (`SELECT, INSERT,
UPDATE`) by the butler runtime roles and `connector_writer`, granted by migration `core_257`.
It SHALL have no `DELETE` grant, and `PUBLIC` SHALL have no privileges on it.

#### Scenario: Runtime roles record exhaustion
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can SELECT, INSERT, and UPDATE `public.provider_allowance_states`
- **AND** it cannot DELETE from it
