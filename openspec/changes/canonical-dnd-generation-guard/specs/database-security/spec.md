## MODIFIED Requirements

### Requirement: Public Schema Write Authorization Matrix

The manifest SHALL enumerate every managed public relation, sequence and function signature, its owner, and each exact role privilege, including column privileges, PUBLIC/default privileges and required RLS. An omitted or future object SHALL confer no runtime, connector or dashboard access. Roster generation SHALL expand identities, never infer object privileges. Every positive row SHALL identify its actual producer, selected pool, permitted verbs and governing contract; unclassified objects SHALL prevent successful finalization. This replaces blanket public grants and the older broad QA accepted-risk posture under the released .6 scope. The active canonical-DND matrix is a predecessor of this same requirement, not a parallel authority to archive later over this successor. Its protected table/guard/audit ownership, enabled and forced RLS, exact invoker/private-definer call chain, current active-role identity, atomic guard/context/audit, replay receipts and snapshot privacy SHALL survive. The ordinary migrator SHALL NOT bypass the canonical writer or directly mutate DND/generation state. The controlled migration installer replaces the bootstrap handoff, not the runtime security boundary. Before any legacy table ownership transition it SHALL validate exact shape, owner and policy/trigger/rule set while holding the required exclusive lock; arbitrary permissive policies, shadow objects or ambiguous pre-guard state SHALL refuse. A protected downgrade SHALL preserve user_context and refuse when durable receipts or advanced generation make restoration unsafe; reapply SHALL never reseed those records. Read policies SHALL keep active DND visible to the existing public readers and not use FOR ALL to hide it.

ID: REQ-database-security-017
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Role-enforced authorized non-DND write survives DND hardening
- **WHEN** a non-owner session under butler_health_rw uses its authorized ordinary context path
- **THEN** the application permission check and non-DND RLS permit that write
- **AND** a migration owner, table owner or privileged setup session does not count as runtime proof

#### Scenario: Direct DND DML cannot cross the RLS boundary
- **WHEN** a runtime or connector directly inserts, updates, clears, deletes or crosses into or out of DND
- **THEN** the statement is rejected before context, guard generation or audit changes

#### Scenario: Canonical DND writer has narrow execution authority
- **WHEN** verified General or Switchboard invokes the canonical gateway with valid correlation
- **THEN** the gateway and private operation independently enforce the active role and requested writer before the atomic change
- **AND** only the minimum invoker-to-definer schema/function call grants exist; other roles and absent role cannot invoke it
- **AND** audit replay receipts remain private to the calling canonical writer, not snapshot/admission readers

#### Scenario: Trusted bootstrap is the sole authority installer
- **WHEN** the predecessor bootstrap-only installation is replaced under this authority rebuild
- **THEN** only the reviewed controlled migrator installer may converge the catalog after explicit owner-provisioned ownership prerequisites
- **AND** serving startup cannot create, adopt, re-own or repair an absent or untrusted interface and an ambiguous predecessor shape fails closed

#### Scenario: Privileged pre-consumer rollback is bounded
- **WHEN** an authorized disposable transition requests rollback before any canonical mutation
- **THEN** actual empty mutation audit and generation zero are proven before the reviewed bounded rollback can preserve rows and restore its declared prior authority profile
- **AND** any receipt or advanced generation refuses before destructive or autocommit work; a newer code rollback never silently reopens broad runtime ownership

#### Scenario: Real PostgreSQL catalog proof establishes the final boundary
- **WHEN** the genuine migrated runtime-role suite exercises the final interface
- **THEN** it proves purpose NOLOGIN ownership, ENABLE plus FORCE RLS, exact predicates, pinned invoker/definer catalogs, and revoked direct/cross-role authority
- **AND** independent committed readback and actual rejection controls are required in addition to source inspection

#### Scenario: Core infrastructure table writes
- **WHEN** a registered infrastructure producer uses its verified role
- **THEN** only its explicit manifest operations succeed: Switchboard/connector ingestion paths retain ingestion_events operations, model producers retain model_round_robin_counters INSERT/UPDATE and token_usage_ledger INSERT
- **AND** ordinary user_context SELECT/non-DND INSERT/UPDATE remain available to the existing runtime and connector roles, while the separate DND fence forbids direct DND or DND-crossing writes

#### Scenario: Identity and contacts table writes
- **WHEN** an actual identity or credential producer writes shared identity state
- **THEN** entities and entity_info receive only the source-proven producer operations recorded in the manifest
- **AND** dropped contacts/contact_info tables receive no ghost privileges; their current owning-domain successors keep their existing access, not a newly shared table

#### Scenario: External account registry table writes
- **WHEN** an existing Google or Steam registry producer uses its selected pool
- **THEN** only its explicitly recorded account-table operations succeed
- **AND** credentials remain DB-authority bound; the manifest grants no new identity, account or secret-resolution authority

#### Scenario: QA and healing table writes
- **WHEN** QA or the dashboard records QA state or an existing still-delivered healing producer writes history
- **THEN** qa_patrols, qa_findings, qa_dismissals and qa_repo_config writes are limited to QA and the actual authenticated dashboard operations
- **AND** qa_allowed_repositories and breaker_resets remain dashboard-controlled; retired Retry/healing execution is not recreated and retained history readers keep their explicit reads

#### Scenario: qa_patrols write access is an accepted trust boundary
- **WHEN** the predecessor broad qa_patrols trust boundary is replaced by this authority model
- **THEN** only butler_qa_rw and the authenticated dashboard synthetic-patrol path may INSERT/UPDATE; no runtime or dashboard role may DELETE patrol history
- **AND** the old accepted risk is an explicit normative successor, not an assertion that broad grants were already safe or that a standalone migration REVOKE persists across bootstrap

#### Scenario: Memory and domain table writes
- **WHEN** a memory or domain producer accesses shared data
- **THEN** memory_catalog has only its actual module producer operations
- **AND** the nonexistent public.facts entry is retired without moving domain facts into public or losing the actual Finance anomaly producer

#### Scenario: Insight pipeline table writes
- **WHEN** the current owning Insight producer or retention job operates
- **THEN** the manifest uses the actual current schema-qualified Insight tables and exact owning role, verbs and retention conditions
- **AND** old public names do not authorize another domain or bypass the existing currentness, owner, eligibility or feedback RLS rules

#### Scenario: Expected-signal producer-owned writes
- **WHEN** a verified producer accesses public.expected_signals
- **THEN** its declared SELECT remains available and INSERT/UPDATE can affect only rows whose producer_role equals its active role
- **AND** forced RLS still prevents replacing another producer signal key

#### Scenario: Dispatch attempt provenance table writes
- **WHEN** a model dispatch producer records provenance
- **THEN** model_dispatch_attempts allows the existing SELECT/INSERT and token_usage_ledger remains append-only
- **AND** unlisted UPDATE/DELETE and history rewriting are denied

#### Scenario: Read-only public tables
- **WHEN** a role accesses a public object outside its positive manifest rows
- **THEN** SELECT and DML are both denied unless that exact operation has a positive row
- **AND** model_catalog and token_limits keep source-proven runtime reads with dashboard-controlled changes; a new public table gets no automatic SELECT

#### Scenario: Adding new public tables to the matrix
- **WHEN** a migration adds or changes a public object used by a serving consumer
- **THEN** the same change supplies its complete exact manifest row, producer provenance and role-level success/refusal controls
- **AND** neither all-role GRANT generation nor a future-object default grant substitutes for that row

#### Scenario: Audit and evidence are append-only
- **WHEN** an actual audit, attention, domain-event or dispatch producer records evidence
- **THEN** only its declared INSERT/read or canonical definer interface is usable; existing immutable ledger and RLS fences continue to apply
- **AND** actual retention deletion is a separate explicit owning row, never permission inferred from an append caller

#### Scenario: Formerly insufficient inventory rows are resolved
- **WHEN** the finalizer encounters entity_info, account registries, spend_rules, secret_probe_log, butler_secrets, model_catalog, deployments, domain-event/subscription state, conversation turns, conditions, state, provider catalogue, Insight retention or historical backup objects
- **THEN** each existing object has an explicit current producer/pool and operation classification before serving admission
- **AND** per-schema state is distinguished from public.state, actual dropped backups are recorded absent, and a dated audit hypothesis or broad fallback cannot mint a grant
### Requirement: Fail-Closed Runtime Role Policy

Butler runtime pools SHALL establish their designated role before serving queries in every environment. Missing or unverifiable roles, inability to assume the role, and missing role configuration for a butler schema SHALL fail startup or the affected connection acquire. No development setting SHALL permit shared-user runtime continuation. Every acquire SHALL re-establish the designated role, including after connection reset or revoked membership. Partial resources owned by a failed connect SHALL be closed without returning a usable connection. Diagnostics SHALL omit DSNs, credentials and private driver payloads while preserving the causal exception for internal diagnosis. This SHALL NOT alter connector pools, dashboard DatabaseManager, managed bootstrap, migration provisioning or grants. Independent DND RLS/ACL, singleton and database-time admission checks SHALL remain required; verified runtime role establishment alone SHALL NOT authorize a DND mutation or durable admission.

ID: REQ-database-security-012
Source: about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md (Database Connection Scoping); bu-wws1eq recorded owner decision (2026-10-21)
Scope: v1-mandatory

#### Scenario: Runtime role cannot be established
- **WHEN** a butler schema lacks its designated role configuration, or role verification or assumption fails
- **THEN** startup or acquisition refuses before returning a usable runtime connection in every environment
- **AND** owned partial resources are closed and diagnostics omit private values
- **AND** no setting permits shared-user runtime continuation or self-bootstrap

#### Scenario: Unprovable DND admission fails closed
- **WHEN** a caller cannot prove its active runtime role, DND RLS/ACL boundary, singleton guard or database-time revalidation
- **THEN** DND mutation and durable admission refuse before DND row, guard, audit or admission changes
- **AND** migration-owner or shared-user authority does not substitute for the required evidence

#### Scenario: Membership revoked before reacquisition
- **WHEN** an established runtime connection is released and the connecting login loses membership in the designated role
- **THEN** the next acquire refuses before returning the connection or executing caller queries
- **AND** successful prior verification or an SSL retry does not disable the setup callback

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
