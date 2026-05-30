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
SET ROLE enforcement SHALL degrade gracefully in environments where runtime roles are absent.

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

### Requirement: Role Membership
The shared database user SHALL be a member of all runtime roles to enable SET ROLE.

#### Scenario: Role membership grant
- **WHEN** the `core_065` migration runs
- **THEN** it executes `GRANT butler_{name}_rw TO CURRENT_USER` for each butler schema
- **AND** it executes `GRANT connector_writer TO CURRENT_USER`
- **AND** this uses `CURRENT_USER` so it works regardless of the migration user's name
- **AND** the grant uses `_execute_best_effort` to tolerate environments where role membership cannot be granted

## Source References
- Non-Negotiable Rule 3 (MCP-only inter-butler communication; SET ROLE is the database-level enforcement mechanism)
- Non-Negotiable Rule 1 (User-federated; the user controls the database and the graceful fallback respects dev environments)
- RFC 0006 (Database Schema and Isolation; defines the schema topology, role naming, and ACL structure that this spec enforces at runtime)
- RFC 0004 (Identity; public schema identity tables are in the write authorization matrix)
- RFC 0009 (Context Bus; `public.user_context` write grants)
