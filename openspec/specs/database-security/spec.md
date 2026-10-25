# Database Security: Role Enforcement Model

## Purpose
Defines the PostgreSQL role-based enforcement model for butler schema isolation. Specifies the SET ROLE mechanism, the public schema write authorization matrix, the connector role model, and the fail-closed runtime role policy.

## Requirements

### Requirement: Runtime Role Enforcement via SET ROLE
Each butler daemon SHALL assume its designated PostgreSQL runtime role on every database connection acquired from the pool. The role constrains the connection's privileges to the butler's own schema plus specifically authorized public table writes.

ID: REQ-database-security-016
Source: RFC 0006 Database Connection Scoping; bu-dss7he.2 delivered runtime boundary; bu-dss7he.6
Scope: v1-mandatory

#### Scenario: Role assumption on pool acquire
- **WHEN** a butler's asyncpg pool acquires a connection
- **THEN** the pool's `setup` callback executes `SET ROLE "butler_{schema}_rw"` before the connection is returned to the caller
- **AND** all subsequent queries on that connection run with the role's privileges
- **AND** when the connection is returned to the pool, asyncpg's `RESET ALL` restores the connecting user's privileges

#### Scenario: Role naming convention
- **WHEN** a butler has schema name `{name}` (e.g., `general`, `health`, `switchboard`)
- **THEN** its runtime role is `butler_{name}_rw` (e.g., `butler_general_rw`, `butler_health_rw`)
- **AND** this convention is defined in `core_001_foundation.py` and must not be changed without a migration

#### Scenario: Role verification at startup
- **WHEN** the `Database.connect()` method is called with a `role` parameter
- **THEN** it opens a temporary connection to check `pg_roles` for the role's existence
- **AND** if the role exists, it sets `_role_verified = True` and registers the `_setup_connection` callback
- **AND** a missing or unreadable role, failed role assumption or missing role configuration for a butler schema refuses before a usable runtime pool is returned in every environment
- **AND** the connecting identity actually assumes the configured role before pool publication, including a lazy pool; owned partial resources are closed on failure or cancellation, with causal errors and content-blind diagnostics
- **AND** the verification connection is closed before pool creation and does not consume a pool slot

#### Scenario: Database class role parameter
- **WHEN** a `Database` instance is created
- **THEN** the `__init__` method accepts an optional `role: str | None` parameter (default `None`)
- **AND** a butler schema with `role` absent or mismatched refuses on connect; schema-less administrative/provisioning utilities remain distinct from butler runtime pools and confer no runtime authority
- **AND** the `from_env()` class method does not set the role -- the caller (lifecycle.py) derives and sets it

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

### Requirement: Connector Role Enforcement

Connectors SHALL establish connector_writer on every pool acquisition and pass the exact connecting-login reach and no-ownership profile before serving. Missing, unreadable or unassumable roles SHALL refuse without a usable pool; the old warning-and-continue behavior is removed. Connector access SHALL be its own declared connectors-schema and exact public manifest operations, not every runtime privilege.

ID: REQ-database-security-018
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Connector SET ROLE
- **WHEN** a connector acquires a connection
- **THEN** the setup callback establishes connector_writer before any connector query
- **AND** only declared connectors-table operations and exact public rows are effective; no automatic public SELECT/CRUD or migration membership is inherited

#### Scenario: Connector role verification
- **WHEN** startup cannot prove the role, login reach profile or role assumption
- **THEN** startup refuses and cleans owned partial resources instead of warning and continuing as the login
- **AND** diagnostics carry fixed categories and retain causal errors internally without DSN, credential or row values

#### Scenario: Connector role utility module
- **WHEN** a connector needs database-role admission
- **THEN** the existing connector role utility enforces the same fixed role and current catalog profile for every real connector consumer
- **AND** failed reacquire after revoked membership does not reuse a cached success

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

### Requirement: Role Membership

Separate operator-provisioned migrator, runtime and dashboard logins SHALL have explicit, disjoint authority profiles. The runtime login MAY reach the roster-derived runtime roles and connector_writer only where that deployed process actually needs them; it SHALL NOT reach migrator, dashboard, owner-auth or purpose-owner roles. Dashboard access SHALL use a dedicated NOLOGIN dashboard capability. Serving roles and every role they can reach SHALL own no managed relation, function, schema or database.

ID: REQ-database-security-019
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Role membership grant
- **WHEN** managed bootstrap establishes or verifies the declared login profiles
- **THEN** the runtime and dashboard membership sets exactly match their declared process profile and unexpected direct or indirect membership refuses admission
- **AND** core_065 best-effort membership on CURRENT_USER is historical behavior to converge, not a way to grant runtime roles to arbitrary migration callers

#### Scenario: Unreachable purpose owners
- **WHEN** a purpose owner or migrator membership edge would become reachable from a serving login
- **THEN** admission refuses even when NOINHERIT prevents immediate inherited privileges
- **AND** SET-capable and indirect edges, ownership and elevated role attributes are checked from the catalog, not only current_user

#### Scenario: Roster changes do not starve Concierge
- **WHEN** a new or existing roster identity is finalized
- **THEN** its own-schema and permitted public rows are derived with the same rules as every other runtime role
- **AND** Concierge is included without copying dated literal role lists or receiving direct specialist-table access

### Requirement: Reviewed Bootstrap Before Supported Alembic Execution

Every supported database SHALL pass read-only catalog admission before any online Alembic extension, schema, version or revision mutation. The operator-only bootstrap SHALL provision the declared roles, extensions and ownership prerequisites; managed application function bodies and final ACLs SHALL be installed and converged by the migrator through Alembic. Admission SHALL verify current role attributes, ownership, exact required signatures/search paths and supported transition state, not an admin singleton or writable completion marker. Missing or unreadable prerequisites SHALL refuse before changes. The new final-state installers SHALL preserve protected DND, attention and restore behavior without the three legacy privileged admin bunkers. Bootstrap SHALL normalize managed normal roles to NOBYPASSRLS and SHALL NOT activate the executor, provide secrets or grant runtime migration/owner authority.

ID: REQ-database-security-011
Source: REQ-database-security-011 existing bootstrap guarantees; bu-dss7he.6 released final-state authority successor; RFC 0006 proposed migration successor
Scope: v1-mandatory

#### Scenario: Existing unbootstrapped core_195 is refused before side effects
- **WHEN** an existing disposable core_195 database lacks the reviewed authority prerequisites and requests an upgrade
- **THEN** admission refuses with a fixed prerequisite category before extension/schema/version changes
- **AND** independent readback proves its core_195 stamp, application rows and object inventory unchanged

#### Scenario: Managed bootstrap permits the same existing database upgrade
- **WHEN** the operator completes the reviewed role/ownership bootstrap and the migrator retries that same database
- **THEN** the real historical-to-final-state migration completes with existing data preserved and exact runtime restrictions
- **AND** no executor secret, executor activation or runtime-owned privileged installer is introduced

#### Scenario: First fresh online migration requires bootstrap
- **WHEN** a fresh database enters the programmatic or direct Alembic path
- **THEN** both paths reject missing prerequisites before side effects and accept the same genuinely prepared catalog
- **AND** migration-created final application tables need not exist before their installer runs

#### Scenario: Finalized repeated and multi-schema migrations remain valid
- **WHEN** a correctly finalized schema repeats its upgrade or another declared schema/module chain upgrades
- **THEN** admission accepts the legitimate current catalog without restoring consumed legacy installer privileges or rerunning privileged bootstrap
- **AND** all-chain revision resolution, private module schemas and planted data remain correct; a documented historical bounded read-default gap is not proof of current read access or permission to restore broad Switchboard defaults

#### Scenario: Normal roles end bootstrap without BYPASSRLS
- **WHEN** an existing managed normal role has BYPASSRLS
- **THEN** admission refuses until the authorized bootstrap removes it, and a rerun is idempotent
- **AND** forced RLS and role-based DND behavior are independently tested after normalization

#### Scenario: Partial bootstrap and misleading objects do not satisfy admission
- **WHEN** an object/profile has wrong owner, signature, definer mode, search path, ACL, membership or required privilege
- **THEN** qualified catalog admission refuses before mutation and does not accept a lookalike, temporary shadow or writable marker
- **AND** independent readback proves private authority state and data unchanged

#### Scenario: Managed surface change requires privileged rerun
- **WHEN** a supported change requires a genuinely new operator-owned role/extension/ownership prerequisite
- **THEN** online admission reports that missing prerequisite until the operator completes the reviewed preparation
- **AND** an ordinary function-body or ACL update owned by the migrator instead runs through its reviewed migration; comments or unrelated stamps attest neither case

#### Scenario: Dev service selection and role safety remain independent
- **WHEN** dev migrates without selecting the restore-drill overlay or supplying executor credentials
- **THEN** the executor remains unenabled and ordinary services retain nonsuperuser, NOBYPASSRLS profiles
- **AND** runtime/dashboard cannot acquire migrator or recovery-owner membership and no Google connector consolidation is activated

#### Scenario: Downgrade and operational failures retain their boundaries
- **WHEN** connection/catalog inspection fails or a caller requests a protected downgrade
- **THEN** failure remains truthful and sanitized; an unauthorized downgrade refuses before newer autocommit mutations
- **AND** only the documented authorized migration/ownership sequence can complete, with independent revision/data readback and no automatic broad-grant rollback

### Requirement: Exact Reachable Ownership and Privilege Admission

Every serving connection SHALL prove its active role and the complete catalog-reachable role set before publication and again on acquisition where session state can reset. Direct, indirect, inherited and SET-capable authority SHALL be included. Serving logins and reachable roles SHALL be nonsuperuser, NOCREATEROLE, NOCREATEDB, NOREPLICATION and NOBYPASSRLS, with no managed-object ownership or unlisted CREATE/admin rights.

ID: REQ-database-security-020
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Unexpected indirect authority is rejected
- **WHEN** an indirect or NOINHERIT membership makes an owner or privileged role reachable
- **THEN** the connection is refused before caller queries, even if its initial SET ROLE succeeded

#### Scenario: Ownership is checked across catalog kinds
- **WHEN** a runtime/dashboard login or reachable role owns a relation, function, schema or database
- **THEN** admission refuses and reports only closed object/role identifiers
- **AND** ordinary migrator ownership and isolated purpose-owner objects do not authorize a serving connection

#### Scenario: Lazy pool and repeated acquire remain fenced
- **WHEN** a lazy or reset pool is used after membership or ACL change
- **THEN** the actual connection is validated before a usable reference is published
- **AND** partial initialization, cancellation and cleanup preserve the original causal failure and leave no unowned tasks

### Requirement: Purpose-Owned Definer Interfaces

Each managed SECURITY DEFINER function SHALL be owned by a fixed purpose-bound NOLOGIN, NOINHERIT, nonprivileged role unreachable from runtime/dashboard logins. It SHALL have the exact search_path pg_catalog, pg_temp with every relation and non-catalog function schema-qualified, an exact signature and EXECUTE ACL, and only the purpose-required underlying permissions. PUBLIC EXECUTE and granting ownership to callers SHALL NOT substitute for explicit access. The migrator SHALL maintain final-state bodies without granting serving roles membership in purpose owners.

ID: REQ-database-security-021
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Catalog exactly matches the function manifest
- **WHEN** a finalizing migration installs managed definers
- **THEN** every prosecdef owner, role attribute, signature, search_path, ACL and required underlying privilege matches its declared row
- **AND** ordinary owner/default ACL expansion or an extra overload is detected, not matched by function name alone

#### Scenario: Permitted call and forbidden direct write diverge
- **WHEN** a declared runtime invokes its purpose interface
- **THEN** the permitted operation commits while direct table mutation, trigger disabling and unauthorized EXECUTE fail under that same runtime identity
- **AND** an independent acquisition observes the committed effect and rollback control observes no effect

#### Scenario: Shadowing and ambient role cannot substitute
- **WHEN** a caller plants a temporary/shadow object or calls as role none or an unlisted role
- **THEN** the purpose operation refuses or resolves only its pinned qualified objects
- **AND** the retained runtime-probe key and authenticated dashboard owner ceremony are not bypassed by the new ownership model

### Requirement: DND Canonical Writer and Generation Fences Survive Rebuild

The authority rebuild SHALL preserve the existing canonical DND gateway/private writer restricted to butler_general_rw and butler_switchboard_rw, FORCE RLS, singleton guard, database-time/current-generation revalidation and atomic audit/admission effects. Existing ordinary non-DND context operations SHALL remain available through their explicit rows. No runtime, dashboard, migration-login fallback or manifest application SHALL make direct DND/crossing writes, generation planting or trigger/policy tampering valid.

ID: REQ-database-security-022
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Direct DND and generation tampering are refused
- **WHEN** a noncanonical runtime or dashboard attempts a DND/crossing mutation, guard update or trigger/policy disable
- **THEN** the database denies it without changing context, guard, audit or admission state

#### Scenario: Canonical mutation is atomic and current
- **WHEN** an existing authenticated canonical writer changes DND while another admission races
- **THEN** the current generation and database time fence determine the result and all context/guard/audit changes commit together
- **AND** stale generation, wrong actor or rollback leaves no partial authorization or admission

#### Scenario: Non-DND context remains useful
- **WHEN** an existing ordinary runtime or connector writes allowed non-DND context
- **THEN** its established INSERT/UPDATE and reads still work under the manifest
- **AND** that positive control cannot mutate DND, cross an existing DND row or grant typed producer authority from an asserted label

### Requirement: One Serialized Migrator Converges the Manifest

The CLI migrator SHALL hold one owned advisory-lock session across all declared roster core/domain/module migrations, final-state interface installation, exact manifest convergence and catalog verification. Its credentials SHALL not be available to serving processes. Repeated application SHALL revoke unlisted managed privileges then grant only declared rows without widening default privileges, changing protected evidence or certifying partial work.

ID: REQ-database-security-023
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Concurrent migrations do not publish partial authority
- **WHEN** two migrate invocations target the same database
- **THEN** only one owns the whole apply sequence at a time and the other waits or refuses within its declared bound
- **AND** the lock is not released between independently opened per-chain engines or before verification

#### Scenario: Reapply cannot re-widen grants
- **WHEN** the same current migration and bootstrap preparation are repeated
- **THEN** effective ACLs/owners stay exactly at the manifest and protected data/stamps remain correct
- **AND** a new unclassified object, missing role or failed verification prevents successful completion

#### Scenario: Work failure or cancellation remains incomplete
- **WHEN** a chain, apply or verification step fails or is cancelled
- **THEN** no successful migration/admission receipt is emitted for partial work and its owned connection/tasks settle
- **AND** a later retry inspects actual durable stamps/catalog rather than trusting an earlier caller acknowledgment

#### Scenario: Core-only and private modules are legitimate targets
- **WHEN** a supported core-only database or actual private Memory schema migrates
- **THEN** only declared chains and schemas are provisioned and catalog-verified
- **AND** cross-schema absence is tested with schema reach first; no blanket grant, private Database field or role bypass is needed

### Requirement: Approved Cross-Schema Exceptions Remain Narrow

The manifest SHALL preserve only the exact adopted RFC 0010 briefing views and QA fixed no-argument scheduler-policy projection, RFC 0030 Concierge telemetry views, and the released named message_inbox readers. A schema/default grant SHALL NOT generalize any exception or grant domain authority.

ID: REQ-database-security-024
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Named Switchboard readers stay narrowed
- **WHEN** Relationship, Messenger or Travel uses its actual inbox consumer
- **THEN** only switchboard.message_inbox receives its required SELECT and no future Switchboard table/default SELECT is granted
- **AND** owning ingestion/interaction/calendar-preparation behavior retains its real consumer proof

#### Scenario: QA local policy read remains legitimate
- **WHEN** the deterministic QA scheduler under butler_qa_rw reads its fixed policy projection
- **THEN** the adopted no-argument current policy/provenance result remains available independently of Switchboard process reachability
- **AND** QA receives no general Switchboard table write/access, no owner-stop bypass and no generic new exception

#### Scenario: Concierge reads only filtered operational views
- **WHEN** Concierge answers a routed system-plane request
- **THEN** its two migration-owned telemetry views return only RFC0030 allowed columns while direct specialist table reads and all cross-schema writes fail
- **AND** prompt/result/tool-call/cost payloads never enter those views

#### Scenario: Briefing and absent retired consumers do not expand access
- **WHEN** General collects scheduled briefing contributions or a stale Decision Desk inventory row is encountered
- **THEN** only the adopted filtered briefing view succeeds and nonexistent retired applier objects receive no grant or new deployment
- **AND** the retired bridge remains absent even with legacy chart values

### Requirement: Retire Mechanisms Only With Executing Security Survivors

The rebuild SHALL remove the three restore-drill/DND/runtime-attention admin bunkers and bootstrap singletons, obsolete runtime-attention v2-v5/interface-version/legacy-debounce ladder, dead calendar role and stored_function_drift parser only when their final-state migration/catalog/behavior survivors execute the same required guarantees. Revision/data history and protected downgrade constraints SHALL survive; source deletion counts or matching test names alone SHALL NOT prove preservation.

ID: REQ-database-security-025
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Old stamps converge without erased guarantees
- **WHEN** a supported old database or fresh whole fleet upgrades
- **THEN** final-state installers preserve restore-drill restrictions, DND fencing and model-breaker/fleet-halt/operator/condition-marker outbox behavior without old admin interfaces
- **AND** existing evidence/data and at-most-once unknown-outcome semantics survive

#### Scenario: Protected downgrade refuses before autocommit
- **WHEN** a normal or unauthorized identity requests a downgrade crossing a protected authority boundary
- **THEN** the refusal happens before any newer autocommit step changes stamps, objects or evidence
- **AND** an authorized disposable transition has its own real catalog/data readback proof

#### Scenario: Deletion has named real survivors
- **WHEN** an old installer, version branch or text-pin test is removed
- **THEN** each lost assertion/parameter/fixture/guarantee maps to an executing role/catalog/behavior survivor with complete cases and meaningful causal removal controls
- **AND** LOC/file deltas are measured against the actual base, and source-reference absence is additional evidence only

#### Scenario: No collateral security retirement
- **WHEN** the original three bunkers and dead calendar role are removed
- **THEN** independent dashboard attention/stepup, owner-auth, restore executor containment and held signing-key authority remain enforced
- **AND** the deferred Google consolidation and migration-baseline successor are not silently implemented

### Requirement: Split Credential Deployment and Operator Cutover

Deployment source SHALL bind migrator credentials only to the migration initialization/CLI action, runtime credentials to actual daemon/control/connector/job consumers, and dashboard credentials to API pools. Dedicated owner-auth credentials SHALL remain separate. Agent-authored source SHALL contain only secret names/references; live login creation, secrets and privileged one-time ownership transfer remain operator actions. Rollback SHALL be an explicit operator-selected release transition, never a missing-key/runtime fallback. The original retained-old-secret, reverted-Helm-env and old-init grant-restoration outcome SHALL have the concrete stage and proof disposition below. An explicitly restored predecessor release is not permission for a serving process of the rebuilt release to use shared-owner credentials.

ID: REQ-database-security-026
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Deployment renders distinct principals
- **WHEN** Helm dev/prod or Compose config is rendered with synthetic secret references
- **THEN** only the migrator action can receive migrator keys and every serving consumer gets its intended credential profile
- **AND** commonEnv or missing-key fallback cannot replace a required profile; Google connector deployment count and dedicated owner-auth wiring stay unchanged

#### Scenario: Existing ownership transfer is explicit
- **WHEN** an existing database still has shared-login-owned objects
- **THEN** read-only preflight reports the exact reviewed transfer set and serving admission refuses until the operator performs the declared ownership steps
- **AND** no application startup self-provisions roles, reads secret values or executes privileged repair

#### Scenario: Rollback preserves the authority boundary
- **WHEN** an operator selects return to the recorded predecessor release before, during or after the authority transition
- **THEN** the procedure stops serving writers and classifies the actual code/catalog/credential combination as R0 (unchanged predecessor), R1 (proven preserving reverse transition), or P (incompatible or unproven), before any init or reversing effect
- **AND** R0 verifies the recorded predecessor code/chart/init digests, actual revision vector and catalog, replays that predecessor init under its original privileged operator authority, and restores that chart's recorded old shared-login secret references through Helm
- **AND** independent acquisitions prove restored predecessor grants, unchanged protected/data/version state and actual predecessor serving readiness before traffic resumes; the old secret remains retained for one release without printing its value
- **AND** R1 requires an exact tested reverse transition that preserves every protected boundary and the predecessor's required interfaces; the operator first executes only that transition under the owned migration/lock path, verifies the recorded predecessor owner/ACL/interface/stamp combination, then follows the same pinned old-init and Helm restoration sequence
- **AND** no unspecified Alembic target, manual version-stamp edit, invented release tag, raw ownership repair or untested old-init replay qualifies as a supported reverse transition
- **AND** P applies if return would remove a DND receipt/current generation, durable attention outbox/lease evidence, protected interface or required data, or no exact compatible reverse transition has been proven; preflight refuses before init, ownership/grant/version changes or newer autocommit effects, preserves the current catalog/data and keeps serving stopped unless a separately verified compatible rebuilt release is restored
- **AND** a receipt or advanced generation alone is not a reason to reject a reverse transition that demonstrably retains that same protected boundary; crossing or losing it is the refusal condition
- **AND** absence of an implemented or proven R1 path remains unfinished preserving-rollback engineering required inside the full105 plan, not a permanent refusal allowance or new owner prerequisite; a current unsafe attempt refuses while that support is built
- **AND** the original unconditional old-init/Helm result is not claimed for the incompatible state: the implementation must identify its exact unmet state/outcome and obtain a specific disposition under the existing adoption process before claiming original rollback acceptance complete
- **AND** neither the positive operator transition nor the refusal installs automatic shared-owner fallback in the rebuilt runtime, changes dedicated owner-auth, or releases the held signing-key decision

#### Scenario: Completion covers the whole released outcome
- **WHEN** the task is considered complete
- **THEN** all original slices, current role/object matrix and behavior survivors have reviewed source and required exact-head proof, or an explicit owner-approved disposition
- **AND** source drafts and live owner actions are distinguished; the held runtime-probe signing-key rehome is re-asked only at actual completed closure

## Source References
- Non-Negotiable Rule 3 (MCP-only inter-butler communication; SET ROLE is the database-level enforcement mechanism)
- Non-Negotiable Rule 1 (User-federated; the owner controls the database, while runtime isolation fails closed)
- RFC 0006 (Database Schema and Isolation; defines the schema topology, role naming, and ACL structure that this spec enforces at runtime)
- RFC 0004 (Identity; public schema identity tables are in the write authorization matrix)
- RFC 0009 (Context Bus; `public.user_context` write grants)
