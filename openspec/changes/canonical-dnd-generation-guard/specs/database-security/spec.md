## MODIFIED Requirements

### Requirement: Public Schema Write Authorization Matrix
Butler runtime roles SHALL retain the migration-managed public-table write
authorization matrix in the canonical specification. In particular,
`public.user_context` keeps its existing broad `INSERT, UPDATE` runtime grants
for ordinary context signals; the application-level context-bus permission
mapping remains responsible for per-signal authorization outside DND.

`dnd` is the sole exception. A core migration SHALL enable and force row-level
security on `public.user_context` so the existing runtime roles and
`connector_writer` can directly write only rows whose `signal_type <> 'dnd'`.
The policy SHALL reject direct DND insert, update, clear, delete, and any update
that crosses into or out of `dnd`. A dedicated no-login DND mutation-owner role
is the only policy principal allowed to write DND rows; it owns the pinned
`SECURITY DEFINER` canonical mutation operation but is not a runtime role and
is not granted to runtime callers. The migration SHALL revoke function execute
from `PUBLIC` and grant it only to `butler_general_rw` and
`butler_switchboard_rw`. No runtime role receives direct write access to the
guard or mutation audit, and no runtime role receives direct audit read access.
The canonical operation alone reads the audit for replay comparison and returns
its receipt only to the calling canonical writer; snapshot/admission readers
receive neither audit rows nor semantic fingerprints.

The RLS policies SHALL constrain writes only. All butlers retain the RFC 0009
public `SELECT` path for active DND state and guarded snapshots; a `FOR ALL`
policy that hides DND from those readers is not permitted.

The canonical operation SHALL prove the connection's active `SET ROLE` identity
and require it to match the requested effective writer. It SHALL not trust a
caller-supplied writer field, `session_user`, or an absent role as authority.
This DND row-level exception does not add peer-schema grants, a shared DSN, or
any direct Health/Messenger access to another butler's records.

#### Scenario: Role-enforced authorized non-DND write survives DND hardening
- **WHEN** a non-owner integration-test session executes `SET ROLE
  butler_health_rw` and writes an authorized non-DND context signal through the
  normal context path
- **THEN** the existing application permission check and the non-DND RLS policy
  permit the write
- **AND** the test does not use the migration owner, table owner, superuser, or
  a privileged setup connection as its runtime proof

#### Scenario: Direct DND DML cannot cross the RLS boundary
- **WHEN** any runtime role or `connector_writer` directly inserts, updates,
  clears, or deletes a DND row, including an update that changes a non-DND row
  into DND or DND into non-DND
- **THEN** the database rejects the statement before it changes a context row,
  guard generation, or mutation audit

#### Scenario: Canonical DND writer has narrow execution authority
- **WHEN** `butler_general_rw` or `butler_switchboard_rw` invokes the canonical
  DND operation under its verified active role with valid correlation
- **THEN** the operation may use its dedicated DND policy owner to make the
  atomic guard/context/audit change
- **AND** Health, Messenger, connectors, and all other roles cannot invoke that
  operation or write DND directly

### Requirement: Graceful Fallback Policy
SET ROLE enforcement SHALL retain its existing graceful development fallback
for ordinary non-DND workloads. When runtime roles are absent, the normal
non-DND context path may continue under the shared database user with the
existing warning and application-level authorization behavior.

The DND mutation and DND-based durable admission are a strict exception. If a
caller cannot prove its active runtime role, the DND RLS/ACL boundary, the
singleton guard, or database-time revalidation, it SHALL fail closed before
changing DND or writing a durable admission. It SHALL not treat a shared-user
development connection, a caller-supplied writer, or migration-owner privilege
as a substitute for verified runtime authority.

#### Scenario: Missing roles do not widen DND authority
- **WHEN** a development environment lacks the required runtime roles or an
  active `SET ROLE` cannot be verified
- **THEN** ordinary non-DND context behavior follows the existing development
  fallback
- **AND** the canonical DND mutation rejects before any DND row, guard, or
  audit change

#### Scenario: Unprovable DND admission fails closed
- **WHEN** a consumer cannot establish its guarded DND snapshot/admission
  boundary because role, RLS/ACL, guard, or database-time evidence is missing
- **THEN** it writes no durable admission and authorizes no external effect

## ADDED Requirements

### Requirement: DND Guard Least-Privilege Mutation Boundary
The DND generation guard and its mutation audit SHALL live in the shared
`public` schema as context-bus infrastructure. Runtime roles may read the
guard only as required for a context snapshot or their own guarded admission.
Only General and Switchboard may invoke the canonical DND mutation operation;
Health, Messenger, connectors, and all other butlers SHALL have no DND mutation
authority.

The core migration SHALL enforce the DND exception with forced row-level
security while preserving the existing application-authorized non-DND context
paths and broad runtime table grants. It SHALL use a dedicated no-login owner
for a pinned `SECURITY DEFINER` canonical DND operation, revoke execute from
`PUBLIC`, and grant only the minimum execute/select privileges required by the
canonical writers and admission readers. It SHALL NOT grant Health, Messenger,
or Switchboard read access to another butler's private schema, a shared DSN, or
a peer queue.

The mutation operation SHALL validate the effective writer identity in addition
to its caller-supplied writer field. Development-mode absence of `SET ROLE` may
not silently widen DND authority: if the operation or a DND-based admission
cannot prove its required authorization, RLS/ACL, guard, and atomic boundary,
it SHALL fail closed.

#### Scenario: Health may read but cannot mutate DND
- **WHEN** Health reads a DND generation snapshot for its policy admission
- **THEN** it reads only the public context-bus guard and canonical public DND
  state
- **AND** an attempt to invoke a DND mutation is rejected before any row,
  generation, or audit change

#### Scenario: Generic direct DND DML is rejected
- **WHEN** any runtime role attempts to insert, update, clear, or delete a DND
  context row without the canonical mutation operation, including a row-type
  crossing update
- **THEN** the database rejects the operation
- **AND** it does not create an unversioned DND state transition

#### Scenario: Authorized non-DND role write remains available
- **WHEN** an actual non-owner runtime-role test session writes an authorized
  non-DND context signal after the DND RLS policy is installed
- **THEN** the normal context path succeeds without access to the DND guard or
  mutation function

#### Scenario: Authorized writer uses the guarded operation
- **WHEN** General or Switchboard invokes the canonical DND mutation operation
  with its own verified role and valid correlation
- **THEN** it receives only the mutation receipt permitted by the context-bus
  contract
- **AND** it gains no access to Health, Messenger, or any other private schema

#### Scenario: Admission remains schema-local
- **WHEN** Messenger performs the final guarded DND admission for a future
  egress intent
- **THEN** it holds the public guard while writing only its own durable record
- **AND** it does not obtain SQL access to an origin butler's deferred queue
