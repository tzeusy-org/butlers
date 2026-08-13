## ADDED Requirements

### Requirement: Reserved Codex Authority Provenance Boundary

The core schema SHALL reserve the public `cli-auth/codex` credential row for
the generation-fenced authority mutation boundary.  It SHALL add a singleton
authority state, opaque generation records, short-lived operation records, and
an opaque generation binding on the existing credential row.  The new records
shall contain no raw credential, token-derived fingerprint/digest, secret
error, PID, file metadata, process/session timestamp heuristic, or capability.

The migration SHALL use fixed-search-path `SECURITY DEFINER` operations owned
by a no-login core role, revoke execution from `PUBLIC`, and grant only the
minimum dashboard shared-authority and designated Codex runtime paths.  Runtime
roles shall not receive direct write access to the state, generation, or
operation relations.  The reserved-row direct-DML guard SHALL make an attempted
legacy/raw mutation unprovable or reject it rather than silently retaining a
stale generation binding.  Existing non-Codex credential storage semantics
remain unchanged.

#### Scenario: Non-Codex credential writes remain available
- **WHEN** an effective runtime role performs an authorized ordinary
  `butler_secrets` write for a key other than `cli-auth/codex`
- **THEN** the existing credential-store authorization path remains available
- **AND** it gains no generation/operation-table write privilege

#### Scenario: Direct reserved-row mutation cannot forge a current binding
- **WHEN** an effective runtime role attempts to insert, update, or delete the
  `cli-auth/codex` row outside the guarded authority operation
- **THEN** the database rejects it or leaves the authority binding unprovable
  according to the migration's compatibility guard
- **AND** a later fence-aware launch cannot treat the raw row as current

#### Scenario: Guarded authority mutation has narrow execution rights
- **WHEN** an authorized dashboard shared-authority path or designated Codex
  runtime path invokes a guarded mutation with valid internal inputs
- **THEN** it may atomically update the reserved credential row and opaque
  provenance state
- **AND** unrelated runtime roles, connectors, and `PUBLIC` cannot invoke the
  mutation operation or write provenance state directly

### Requirement: Provenance Expiry and Retention Security

The database SHALL enforce operation-state constraints, one exact current
generation binding, and terminal-only operation garbage collection.  Database
clock timestamps may implement expiry/retention but SHALL not be used as
authority ordering.  Cleanup SHALL delete only terminal, value-free operation
metadata at least 90 days after terminalization and SHALL not delete a current
generation, an operation-referenced generation, or a raw credential row.

#### Scenario: Effective-role cleanup cannot delete current authority
- **WHEN** the authorized deterministic cleanup path processes expired or old
  Codex operation records
- **THEN** it may mark expired operations terminal and remove eligible terminal
  metadata only
- **AND** it cannot remove the current generation binding or the existing
  `cli-auth/codex` credential row

#### Scenario: Provenance relations are not a dashboard read surface
- **WHEN** a browser-facing Secrets/API request, generic audit query, or
  non-owner runtime role reads credential evidence
- **THEN** it receives only the existing value-free credential evidence shape
- **AND** it cannot read operation IDs, generation IDs, lineage, raw values,
  derived identifiers, or secret-bearing terminal reasons
