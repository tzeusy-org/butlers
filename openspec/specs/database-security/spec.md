# Database Security: Role Enforcement Model

## Purpose
Defines the PostgreSQL role-based enforcement model for butler schema isolation. Specifies the SET ROLE mechanism, the public schema write authorization matrix, the connector role model, and the graceful fallback policy.

## Requirements

### Requirement: Runtime Role Enforcement via SET ROLE
Each butler daemon SHALL assume its designated PostgreSQL runtime role on every database connection acquired from the pool. The role constrains the connection's privileges to the butler's own schema plus specifically authorized public table writes.

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
- **AND** if the role does not exist, it logs a warning and creates the pool without the callback
- **AND** the verification connection is closed before pool creation and does not consume a pool slot

#### Scenario: Database class role parameter
- **WHEN** a `Database` instance is created
- **THEN** the `__init__` method accepts an optional `role: str | None` parameter (default `None`)
- **AND** when `role` is `None`, no SET ROLE enforcement occurs (backward compatible)
- **AND** the `from_env()` class method does not set the role -- the caller (lifecycle.py) derives and sets it

### Requirement: Public Schema Write Authorization Matrix
Butler runtime roles SHALL have write access to a specific set of public tables. The authorization matrix is maintained as a migration-managed grant set.

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

#### Scenario: qa_patrols write access is an accepted trust boundary
- **WHEN** the runtime-role grants on `public.qa_patrols` are reviewed
- **THEN** every runtime role retaining INSERT and UPDATE (and the broad public baseline leaving DELETE effectively open) is an explicitly accepted risk, not a defect
- **AND** the accepted risk is that a compromised or defective runtime role could insert a qualifying row that masks or resolves the paging QA-patrol-overdue condition, forge a recorded handoff mode, or delete patrol rows
- **AND** narrowing the access (bootstrap ACL finalizer or forced RLS) requires a separate change, because a migration `REVOKE` alone does not persist against `scripts/init-db.sql` re-grants

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

### Requirement: Connector Role Enforcement
Connectors SHALL use the `connector_writer` role for database access, enforced via the same SET ROLE mechanism as butler roles.

#### Scenario: Connector SET ROLE
- **WHEN** a connector process creates an asyncpg connection pool
- **THEN** it passes a `setup` callback that executes `SET ROLE "connector_writer"` on every connection acquire
- **AND** this grants the connector: full CRUD on `connectors.*` tables, SELECT on all public tables, and the same targeted write grants on public tables as butler roles

#### Scenario: Connector role verification
- **WHEN** a connector starts up
- **THEN** it verifies `connector_writer` exists in `pg_roles`
- **AND** if the role does not exist, it logs a warning and operates without SET ROLE (same fallback as butler roles)

#### Scenario: Connector role utility module
- **WHEN** a connector needs SET ROLE enforcement
- **THEN** it imports `connector_setup_role` from `src/butlers/connectors/db_role.py`
- **AND** the utility provides a pre-built asyncpg setup callback and a role verification helper

### Requirement: Graceful Fallback Policy

SET ROLE enforcement SHALL degrade gracefully in environments where runtime roles are absent. The system SHALL retain the existing development fallback for direct runtime connection setup when PostgreSQL roles cannot be verified, including the warning and shared-user connection behavior. This fallback SHALL NOT waive REQ-database-security-011: a supported daemon or launcher that requests online migrations SHALL satisfy the reviewed bootstrap prerequisite first, and missing provenance SHALL stop that migration/startup path even under dev. No fallback SHALL authorize self-bootstrap or recovery privileges. SET ROLE enforcement SHALL retain its existing graceful development fallback for ordinary non-DND workloads. When runtime roles are absent, the normal non-DND context path may continue under the shared database user with the existing warning and application-level authorization behavior. The DND mutation and DND-based durable admission are a strict exception. If a caller cannot prove its active runtime role, the DND RLS/ACL boundary, the singleton guard, or database-time revalidation, it SHALL fail closed before changing DND or writing a durable admission. It SHALL not treat a shared-user development connection, a caller-supplied writer, or migration-owner privilege as a substitute for verified runtime authority.

ID: REQ-database-security-012
Source: about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md (Database Connection Scoping); bu-kqnum.8.10 owner decision and clarification (2026-08-15)
Scope: v1-mandatory

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
- **AND** the request is direct development runtime connection setup and requests no online migration
- **THEN** the roles do not exist in `pg_roles`
- **AND** `Database.connect()` detects this and skips the `setup` callback
- **AND** a warning is logged: "Role {role} not found; SET ROLE enforcement disabled. Butler {name} runs with shared-user privileges."
- **AND** all queries execute with the shared database user's privileges (identical to pre-enforcement behavior)
- **AND** no error is raised -- the butler starts and operates normally

- **AND** this direct runtime continuation does not waive a supported online migration/startup prerequisite

#### Scenario: Enforcement in production
- **WHEN** the PostgreSQL instance has roles created by the migration (production default)
- **THEN** SET ROLE enforcement is active for all butler and connector connections
- **AND** the connecting user (`butlers`) must be a member of each runtime role (granted by `core_065`)
- **AND** any query that violates the role's privileges fails with a PostgreSQL permission error

### Requirement: Role Membership
The shared database user SHALL be a member of all runtime roles to enable SET ROLE.

#### Scenario: Role membership grant
- **WHEN** the `core_065` migration runs
- **THEN** it executes `GRANT butler_{name}_rw TO CURRENT_USER` for each butler schema
- **AND** it executes `GRANT connector_writer TO CURRENT_USER`
- **AND** this uses `CURRENT_USER` so it works regardless of the migration user's name
- **AND** the grant uses `_execute_best_effort` to tolerate environments where role membership cannot be granted

### Requirement: Reviewed Bootstrap Before Supported Alembic Execution

Every supported database, including ordinary dev, SHALL complete the reviewed `scripts/init-db.sql` using its distinct privileged cluster-superuser bootstrap identity before its first online Alembic migration, and SHALL rerun that managed procedure when its managed role/schema/interface surface changes. Online programmatic and direct Alembic admission SHALL read the actual target database's current catalog before extension, target-schema, version-table or revision mutations; missing, unreadable or untrusted required bootstrap provenance SHALL fail closed with a bounded prerequisite error pointing to `scripts/init-db.sql`. The check SHALL validate required extensions, the current bootstrap-managed runtime-role/schema and migration-membership/default-ACL profile, and the exact trusted protected admin signatures/ownership/definer/search-path profile, without executing installers, issuing grants, changing roles or trusting a writable completion marker. Installer-ready and legitimately finalized protected states SHALL both be accepted: completion may consume one-shot installer grants, and a repeated or second-schema migration SHALL NOT demand those consumed grants when the boundary is correctly finalized. Applied protected migrations SHALL retain their independent state-specific guards. The managed bootstrap prerequisite SHALL NOT enable the executor, supply its secret, activate a drill or widen normal migration/runtime privileges; ordinary dev SHALL retain its base-only service selection. Connectivity/authentication errors SHALL remain distinguishable from a successfully inspected missing prerequisite, and diagnostics SHALL omit connection credentials, private row contents and raw SQL values. Supported privileged downgrade operations SHALL retain their existing managed authorization and data-preservation constraints; a bootstrap check SHALL NOT authorize a normal role to perform them. No invocation SHALL run the privileged script blindly before every migration, self-bootstrap, waive provenance or mark a partial migration as complete. The required default-ACL admission profile SHALL cover stable bootstrap authority and own-schema/base privileges; ordinary migration-owned Relationship SELECT defaults on Switchboard SHALL remain outside that profile because applied core_001 revokes them during legitimate bounded replay and core_077 repairs them. Their absence SHALL NOT waive any stable required privilege or attest working runtime read access; actual catalog and effective-role readback SHALL independently distinguish the bounded absent state from the repaired state without trusting a revision, marker or object name as provenance.

ID: REQ-database-security-011
Source: bu-kqnum.8.10 owner decision and clarification (2026-08-15); about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md; scripts/init-db.sql; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Existing unbootstrapped core_195 is refused before side effects
- **WHEN** a genuine disposable database has the complete historical core chain applied through core_195 and lacks trusted managed bootstrap provenance, and its ordinary migration login requests an online upgrade
- **THEN** admission fails with the bounded bootstrap-prerequisite error naming `scripts/init-db.sql`, while separate readback confirms unchanged core_195 tracking, planted application data and the extension/schema/version/object inventory captured before the request

#### Scenario: Managed bootstrap permits the same existing database upgrade
- **WHEN** the distinct disposable bootstrap superuser successfully runs the checked-in fail-fast `scripts/init-db.sql` for that database and its actual normal migration login, with no executor secret or enablement, and the normal login retries the upgrade
- **THEN** core_196 and the requested supported later revisions complete, a separate normal-login readback witnesses the expected revision and planted data, and genuine runtime-role controls retain the protected interface restrictions

#### Scenario: First fresh online migration requires bootstrap
- **WHEN** a fresh supported database is inspected through either the programmatic runner or direct online Alembic entry point before its first migration
- **THEN** absent prerequisites refuse before extension, schema or version-table DDL, whereas the same request after the real managed bootstrap succeeds without requiring migration-created protected tables to exist in advance

#### Scenario: Finalized repeated and multi-schema migrations remain valid
- **WHEN** the first schema has legitimately finalized the protected interfaces and consumed their one-shot installer grants, and the normal login repeats its upgrade or upgrades another target schema with multiple known chain heads
- **THEN** read-only admission accepts the legitimate finalized state, all-chain revision resolution and per-schema tracking remain correct, planted data survives, and no installer privilege is restored or privileged bootstrap repeated automatically
- **AND** a genuine checked-init, bounded core_076 and second-schema continuation remains admitted while the ordinary Relationship-to-Switchboard read default is absent, required own-schema privilege loss still refuses without catalog changes, and immutable core_077/head repair restores both the read default and actual runtime SELECT without losing the planted row

#### Scenario: Partial bootstrap and misleading objects do not satisfy admission
- **WHEN** required managed catalog entries are absent or have wrong ownership, signatures, definer mode, pinned search path, membership or required privileges, including a caller-owned lookalike, a temporary/catalog shadow, or a caller-writable completion marker
- **THEN** admission refuses before migration side effects using qualified catalog identities, with a genuinely bootstrapped companion accepted; unchanged private authority objects and data are witnessed independently

#### Scenario: Managed surface change requires privileged rerun
- **WHEN** the reviewed bootstrap and its checked-in prerequisite profile gain a managed role/schema/interface surface that is absent from an existing supported database
- **THEN** the next online admission reports the missing prerequisite and permits retry only after the authorized managed bootstrap rerun; changing comments, a caller label or an unrelated migrated revision cannot satisfy that profile

#### Scenario: Dev service selection and role safety remain independent
- **WHEN** an ordinary dev database is bootstrapped and migrated without selecting the protected restore-drill overlay or providing an executor secret
- **THEN** its normal migration and runtime roles remain nonsuperuser without self-bootstrap or recovery-owner membership, the executor remains unenabled, and base-only services can proceed without weakening the isolated executor or dashboard read-only boundary

#### Scenario: Downgrade and operational failures retain their boundaries
- **WHEN** an online operation encounters connection failure, insufficient catalog visibility, a normal-role request to cross a protected downgrade boundary, or an authorized managed privileged downgrade followed by rebootstrap and upgrade
- **THEN** errors remain truthful and sanitized, unreadable provenance never admits migration, normal roles cannot drop the protected boundary, and only the existing authorized disposable privileged sequence may complete with its actual revision/data state independently read back

## Source References
- Non-Negotiable Rule 3 (MCP-only inter-butler communication; SET ROLE is the database-level enforcement mechanism)
- Non-Negotiable Rule 1 (User-federated; the user controls the database and the graceful fallback respects dev environments)
- RFC 0006 (Database Schema and Isolation; defines the schema topology, role naming, and ACL structure that this spec enforces at runtime)
- RFC 0004 (Identity; public schema identity tables are in the write authorization matrix)
- RFC 0009 (Context Bus; `public.user_context` write grants)
