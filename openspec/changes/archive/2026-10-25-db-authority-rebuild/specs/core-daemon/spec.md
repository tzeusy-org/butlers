## ADDED Requirements

### Requirement: Read-Only Migration Admission Before Serving

Daemon startup SHALL verify the required core/domain/declared-module revisions and current authority profile read-only using its serving identity before starting affected consumers. Only the separate CLI migrator may execute schema upgrades. Existing startup ordering, module dependency cascades, private Memory schemas, configuration seeding and finite resource ownership SHALL remain; a failed admission SHALL not run a handler, pretend an empty database or self-bootstrap.

ID: REQ-core-daemon-008
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Current migrated startup succeeds without migrator credentials
- **WHEN** the actual daemon with declared modules starts against a valid current catalog
- **THEN** it verifies exact required revisions/interfaces before its existing config seeds, tools and schedules become usable
- **AND** no runtime migration connection or migrator credential is requested

#### Scenario: Core admission failure stops serving
- **WHEN** a required core/domain revision or authority profile is absent, stale or unreadable
- **THEN** startup fails before consumers can query or mutate application state
- **AND** diagnostics are bounded and content-blind and owned partial resources are closed

#### Scenario: Module admission retains dependency semantics
- **WHEN** a declared module private schema/revision is unavailable
- **THEN** that module and its dependent consumers retain the existing explicit failed/cascade semantics
- **AND** unrelated healthy modules do not acquire its authority and a configured private Memory schema is checked by its actual chain/schema identity

#### Scenario: Cancellation owns work through release
- **WHEN** startup admission is cancelled or a partial pool fails
- **THEN** the caller receives no usable unverified pool and the actual connection/release work stays owned until settlement
- **AND** shared pools are not closed under unresolved owners and original cleanup budgets/errors remain intact

#### Scenario: Configuration startup does not become DDL authority
- **WHEN** a current runtime seeds config or synchronizes schedules after successful admission
- **THEN** its existing authorized row writes and actual scheduler CAS behavior remain available
- **AND** no missing-table fallback or added ALTER/CREATE grant replaces the required migrated schema
