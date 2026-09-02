## MODIFIED Requirements

### Requirement: Public Schema Write Authorization Matrix
Butler runtime roles SHALL retain the migration-managed public-table write
authorization matrix in the canonical specification. In particular,
`public.user_context` keeps its existing broad `INSERT, UPDATE` runtime grants
for ordinary context signals; the application-level context-bus permission
mapping remains responsible for per-signal authorization outside DND.

`dnd` is the sole exception. A trusted cluster-superuser bootstrap installer and
finalizer SHALL establish the boundary; an ordinary core migration SHALL only
catalog-validate that trusted interface or invoke its fixed no-argument
installer. The ordinary migration SHALL NOT create, own, re-own, repair, or
adopt the DND table, guard, audit, owner role, policy, gateway, or private
definer.

A planned core downgrade MAY invoke only a separately catalog-proven,
no-argument bootstrap rollback routine. It SHALL require a trusted superuser,
an empty durable mutation audit, and a singleton guard at generation `0`; it
SHALL otherwise fail before destructive DDL. The routine SHALL preserve
`public.user_context` rows, restore the recorded migration-role ownership and
ordinary non-DND RLS posture, and re-grant only the fixed installer handoff. It
SHALL NOT drop/reseed a receipt-bearing or advanced generation boundary.

A managed trusted-superuser down/up MAY then invoke the same catalog-proven
installer. This does not grant the ordinary migration role finalizer or rollback
authority.

Before it transfers the existing shared table, the installer SHALL prove the
complete known `public.user_context` column/key shape, the recorded migration
role as its owner, and an ordinary disabled-RLS posture; it SHALL reject every
pre-existing user policy, trigger, or rewrite rule. It SHALL not accept an
arbitrary permissive RLS policy beside the guarded set, because permissive
policies compose with OR and could reopen direct DND DML, nor an ambiguous
pre-guard state that the bounded rollback could not restore.
It SHALL hold an `ACCESS EXCLUSIVE` lock through this validation and final
ownership handoff so the former shared-table owner cannot race a policy,
trigger, or shape change.

The finalized interface SHALL make a dedicated NOLOGIN, non-superuser,
non-BYPASSRLS owner with no runtime/migration membership the owner of
`public.user_context`, `public.dnd_generation_guard`,
`public.dnd_generation_mutations`, the DND policies, and private pinned
definer. It SHALL both enable and force row-level security on
`public.user_context` so the existing runtime roles and `connector_writer` can
directly write only rows whose `signal_type <> 'dnd'`. The policy SHALL reject
direct DND insert, update, clear, delete, and any update that crosses into or
out of `dnd`.

The finalizer SHALL revoke `PUBLIC`, ordinary migration-role, and unapproved
runtime direct DML/DDL/function authority; it SHALL grant no runtime role
direct guard/audit access or audit read access. A pinned `SECURITY INVOKER`
gateway SHALL first prove `current_user` is the active General/Switchboard role
and that it matches the requested writer, then call a private pinned
`SECURITY DEFINER` operation. The private operation SHALL independently verify
`current_setting('role', true)` because its `current_user` is the NOLOGIN owner.
`PUBLIC` receives no execute grant; only `butler_general_rw` and
`butler_switchboard_rw` receive the minimum gateway grant and the private
function/schema call-chain grants PostgreSQL requires for an invoker gateway to
reach its definer. Those private grants do not create a second authority path:
the private function independently rejects an absent or mismatched active role
and writer. The private operation alone reads the audit for replay comparison
and returns its receipt only to the calling canonical writer; snapshot/admission
readers receive neither audit rows nor semantic fingerprints.

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

#### Scenario: Trusted bootstrap is the sole authority installer
- **WHEN** an ordinary core migration encounters an absent DND interface or a
  pre-existing interface with untrusted owner, ACL, policy, search path, or
  function-security catalog state
- **THEN** it only invokes a catalog-proven trusted bootstrap installer for the
  absent case and otherwise fails closed
- **AND** it never creates, adopts, re-owns, repairs, or grants authority to the
  observed DND objects

#### Scenario: Privileged pre-consumer rollback is bounded
- **WHEN** a trusted-superuser core downgrade finds an empty mutation audit and
  guard generation `0`
- **THEN** it invokes only the catalog-proven bootstrap rollback routine, which
  preserves `public.user_context` rows and restores the pre-guard handoff
- **AND** any durable receipt or nonzero generation fails before it removes the
  DND authority objects

#### Scenario: Real PostgreSQL catalog proof establishes the final boundary
- **WHEN** the authorized role/catalog integration suite runs against actual
  PostgreSQL after the trusted installer/finalizer
- **THEN** it proves the NOLOGIN ownership, `ENABLE` plus `FORCE RLS`, exact
  policy predicates, pinned invoker/definer attributes, and revocation of
  `PUBLIC`, migration-role, direct, and cross-role DND authority
- **AND** static source inspection alone is not accepted as that proof

#### Scenario: Core infrastructure table writes
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can write to these core infrastructure public tables:
  - `public.ingestion_events` — INSERT, UPDATE, DELETE (ingestion pipeline, owntracks retention)
  - `public.user_context` — INSERT, UPDATE (context bus, RFC 0009)
  - `public.model_round_robin_counters` — INSERT, UPDATE (model routing)
  - `public.token_usage_ledger` — INSERT (token tracking)

#### Scenario: Identity and contacts table writes
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can write to these identity public tables:
  - `public.entities` — INSERT, UPDATE, DELETE (identity module, bootstrap)
  - `public.contacts` — INSERT, UPDATE (contacts module)
  - `public.contact_info` — INSERT, UPDATE, DELETE (contacts, relationship)
  - `public.entity_info` — INSERT, UPDATE, DELETE (credentials, entity management)

#### Scenario: External account registry table writes
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can write to these account registry public tables:
  - `public.google_accounts` — INSERT, UPDATE (Google OAuth registry)
  - `public.steam_accounts` — INSERT, UPDATE, DELETE (Steam account registry)

#### Scenario: QA and healing table writes
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can write to these QA public tables:
  - `public.healing_attempts` — INSERT, UPDATE
  - `public.qa_dismissals` — INSERT, UPDATE, DELETE
  - `public.qa_findings` — INSERT, UPDATE
  - `public.qa_repo_config` — UPDATE
  - `public.qa_patrols` — INSERT, UPDATE

#### Scenario: Memory and domain table writes
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can write to these domain public tables:
  - `public.memory_catalog` — INSERT, UPDATE (memory module)
  - `public.facts` — INSERT, UPDATE (finance anomaly detection, ON CONFLICT DO UPDATE)

#### Scenario: Insight pipeline table writes
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can write to these insight public tables:
  - `public.insight_candidates` — INSERT, UPDATE, DELETE (insight broker)
  - `public.insight_cooldowns` — INSERT, DELETE (cooldown tracking)
  - `public.insight_engagement` — INSERT, UPDATE, DELETE (engagement tracking)
  - `public.insight_settings` — INSERT, UPDATE (delivery settings)

#### Scenario: Expected-signal producer-owned writes

- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can SELECT from `public.expected_signals`
- **AND** it can INSERT or UPDATE only rows whose `producer_role` equals its active runtime role
- **AND** forced row-level security prevents one runtime role from replacing another role's signal key

#### Scenario: Dispatch attempt provenance table writes
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can write to the dispatch attempt provenance table:
  - `public.model_dispatch_attempts` — SELECT, INSERT (failover provenance, core_104 migration)

#### Scenario: Read-only public tables
- **WHEN** a butler operates under SET ROLE enforcement
- **THEN** it can only SELECT (not INSERT, UPDATE, or DELETE) from public tables not in the write authorization matrix
- **AND** this includes `public.model_catalog`, `public.token_limits`, and any future public tables that do not have explicit write grants

#### Scenario: Adding new public tables to the matrix
- **WHEN** a new public table is created by a migration and butlers need to write to it
- **THEN** a subsequent core migration SHALL add targeted GRANT statements for that table to all butler runtime roles
- **AND** the write authorization matrix in this spec SHALL be updated
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

#### Scenario: Missing roles in development
- **WHEN** the `core_001_foundation` migration ran but could not create roles (e.g., connecting user lacks CREATEROLE)
- **THEN** the roles do not exist in `pg_roles`
- **AND** `Database.connect()` detects this and skips the `setup` callback
- **AND** a warning is logged: "Role {role} not found; SET ROLE enforcement disabled. Butler {name} runs with shared-user privileges."
- **AND** all queries execute with the shared database user's privileges (identical to pre-enforcement behavior)
- **AND** no error is raised -- the butler starts and operates normally

#### Scenario: Enforcement in production
- **WHEN** the PostgreSQL instance has roles created by the migration (production default)
- **THEN** SET ROLE enforcement is active for all butler and connector connections
- **AND** the connecting user (`butlers`) must be a member of each runtime role (granted by `core_065`)
- **AND** any query that violates the role's privileges fails with a PostgreSQL permission error
## ADDED Requirements

### Requirement: DND Guard Least-Privilege Mutation Boundary
The DND generation guard and its mutation audit SHALL live in the shared
`public` schema as context-bus infrastructure. Runtime roles may read the
guard only as required for a context snapshot or their own guarded admission.
Only General and Switchboard may invoke the canonical DND mutation operation;
Health, Messenger, connectors, and all other butlers SHALL have no DND mutation
authority.

The trusted finalizer SHALL enforce the DND exception with `ENABLE` plus
`FORCE` row-level security while preserving the existing application-authorized
non-DND context paths and broad runtime table grants. It SHALL use its dedicated
NOLOGIN owner for a private pinned `SECURITY DEFINER` canonical DND operation
behind a pinned `SECURITY INVOKER` active-role gateway, revoke execute from
`PUBLIC`, and grant only the minimum gateway execute/select privileges required
by canonical writers and admission readers. It SHALL NOT grant Health,
Messenger, or Switchboard read access to another butler's private schema, a
shared DSN, or a peer queue.

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
