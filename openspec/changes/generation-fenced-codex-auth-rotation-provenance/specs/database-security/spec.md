## ADDED Requirements

### Requirement: Reserved Codex Authority Provenance Boundary

The core schema SHALL reserve the public `cli-auth/codex` credential row for
the generation-fenced authority mutation boundary.  It SHALL add a singleton
authority state, opaque generation records, short-lived operation records, and
an opaque generation binding on the existing credential row.  The new records
shall contain no raw credential, token-derived fingerprint/digest, secret
error, PID, file metadata, process/session timestamp heuristic, or capability.

A privileged bootstrap SHALL install a fixed, no-argument installer/finalizer
owned by a trusted bootstrap principal. The normal migration SHALL catalog-
verify and invoke that installer rather than create or own the protected
objects. The installed fixed-search-path `SECURITY DEFINER` operations and
relations SHALL be owned by a NOLOGIN, non-member core role. The finalizer
SHALL revoke owner membership, protected-schema `CREATE`, direct relation DML,
installer/finalizer execution, and every unneeded privilege from the normal
migration login, runtime roles, connectors, and `PUBLIC`.

Because bootstrap grants broad DML on existing `public` tables, the finalizer
SHALL repair `public.butler_secrets` after every broad-grant bootstrap rerun. It
SHALL enable and FORCE row-level security, remove every non-system policy, and
install exactly a non-reserved-row policy for established callers plus a
reserved-row policy for the membership-free NOLOGIN definer owner. It SHALL
revoke table-level SELECT/UPDATE and all direct SELECT/UPDATE privilege on
`codex_auth_generation_id`, then restore only precise legacy-column privileges
needed for non-Codex CRUD. No normal role may read the reserved row or its
generation binding directly. The fixed definer owner alone may cross that RLS
policy; a migration owner or trigger is not sufficient isolation.

The normal runtime/prewarm/probe prepare operation SHALL have no bootstrap
parameter. A distinct device-auth bootstrap function SHALL be executable only
by the dashboard API's no-`SET ROLE` shared-authority path, not any effective
runtime role, and SHALL derive
`bootstrap_absent` from locked database state. Runtime roles shall not receive
direct write access to the state, generation, or operation relations. The
reserved-row direct-DML guard SHALL make an attempted legacy/raw mutation
unprovable or reject it rather than silently retaining a stale generation
binding. Existing non-Codex credential storage semantics remain unchanged.

#### Scenario: Non-Codex credential writes remain available
- **WHEN** an effective runtime role performs an authorized ordinary
  `butler_secrets` write for a key other than `cli-auth/codex`
- **THEN** the existing credential-store authorization path remains available
- **AND** it gains no generation/operation-table write privilege

#### Scenario: Broad bootstrap grants are repaired deterministically
- **WHEN** privileged bootstrap is rerun and reapplies its historical broad
  `public` table grants
- **THEN** the Codex finalizer restores FORCE RLS, the exact reserved/non-
  reserved policies, and precise legacy-column privileges
- **AND** an effective runtime role can still perform ordinary non-Codex CRUD
  but cannot select the reserved row or `codex_auth_generation_id`

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

#### Scenario: Runtime preparation cannot invoke device-auth bootstrap
- **WHEN** an effective role that may prepare normal Codex runtime operations
  attempts to invoke the protected device-auth bootstrap function
- **THEN** PostgreSQL rejects the invocation for insufficient privilege
- **AND** the dashboard API's no-`SET ROLE` shared-authority path can invoke it
  only through the fixed definer function and still cannot write provenance
  relations directly

#### Scenario: Upgraded database requires privileged installer ordering
- **WHEN** an already-provisioned database runs the normal provenance migration
  before the privileged bootstrap has installed the exact trusted installer
- **THEN** the migration fails closed without creating or taking ownership of
  protected objects
- **AND** after the privileged bootstrap runs, the normal migration can invoke
  the fixed installer and retains no owner membership, protected-schema
  `CREATE`, direct-DML, or installer/finalizer privilege

#### Scenario: Provenance migration downgrade is intentionally irreversible
- **WHEN** an operator or migration harness invokes the provenance migration's
  `downgrade()`
- **THEN** it fails transactionally before issuing schema or ACL DDL
- **AND** the applied revision, raw row, value-free provenance, FORCE RLS,
  policies, and privileges remain unchanged
- **AND** any future removal requires a future independently reviewed migration

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
