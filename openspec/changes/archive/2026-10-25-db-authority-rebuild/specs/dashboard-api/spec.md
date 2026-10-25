## MODIFIED Requirements

### Requirement: DatabaseManager (Per-Butler Pool Management)
`src/butlers/api/db.py` SHALL maintain one asyncpg connection pool per butler and a dedicated shared credential pool. Supports both legacy multi-DB and one-DB/multi-schema topologies.

ID: REQ-dashboard-api-073
Source: bu-dss7he.6; RFC 0006 proposed dashboard authority successor; database-security role model
Scope: v1-mandatory

#### Scenario: Add butler pool
- **WHEN** `add_butler(name, db_name, db_schema)` is called
- **THEN** an asyncpg pool is created with `search_path` set to `"{schema}", public` when a schema is specified
- **AND** the pool is cached by butler name for subsequent lookups

#### Scenario: Shared credential pool
- **WHEN** `set_credential_shared_pool(db_name, db_schema)` is called
- **THEN** a separate pool is created for the shared credential database
- **AND** `credential_shared_pool()` returns it (or raises `KeyError` if not configured)

#### Scenario: Canonical database target registration
- **WHEN** dashboard API startup configures per-butler and shared credential pools
- **THEN** each `DatabaseManager` registration receives the resolved `Database.db_name`
- **AND** a non-empty `DATABASE_URL` uses its decoded database path ahead of `POSTGRES_DB` or the roster fallback
- **AND** a `DATABASE_URL` without a database path fails startup before any manager pool is opened

#### Scenario: Pool lookup
- **WHEN** `pool(butler_name)` is called
- **THEN** the cached asyncpg pool for that butler is returned
- **AND** `KeyError` is raised if the butler has not been added

#### Scenario: Schema-scoped search path
- **WHEN** a butler has `db_schema` configured (e.g., `"switchboard"`)
- **THEN** the pool's `search_path` server setting is `"{schema}", shared, public`
- **AND** all queries on that pool are scoped to the butler's schema by default

#### Scenario: Dashboard authority is established before publication
- **WHEN** the manager creates any per-butler or shared credential pool
- **THEN** the dedicated dashboard login establishes its fixed dashboard capability and exact reachable-role/no-ownership profile before the pool is cached or returned
- **AND** every acquire restores and validates that role; owner-auth uses its separate dedicated pool rather than borrowing dashboard authority

#### Scenario: Role failure is not a healthy empty source
- **WHEN** the dashboard role, required object privilege or current catalog profile cannot be established
- **THEN** creation/acquisition fails with bounded sanitized diagnostics and no usable partial pool
- **AND** existing fan-out degradation and selected credential-pool semantics remain truthful, including native private Memory schema consumers

## ADDED Requirements

### Requirement: Database Authority Catalog Facts Replace Bootstrap Text Drift

The dashboard SHALL report actual managed database ownership, effective ACL, role-reach, definer signature/search_path and migration-profile comparison facts without parsing init-db.sql bodies. Its fixed read-only catalog capability SHALL not grant migration or private row access. Unavailable catalog evidence SHALL remain distinct from a verified match, an absent managed object and a mismatch; responses/logs SHALL contain only closed identifiers, counts/categories and digests, never function bodies or secret values.

ID: REQ-dashboard-api-074
Source: bu-dss7he.6 owner release 2026-10-20; RFC 0006 proposed authority successor; about/heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Verified current catalog is a match
- **WHEN** the read-only authority check completes against the expected manifest and current migrations
- **THEN** the API/UI shows verified matching facts bound to that database/profile rather than an inferred clean state

#### Scenario: Missing and mismatched objects are explicit
- **WHEN** a managed object is absent or its owner, role reach, signature, ACL or search path differs
- **THEN** the API/UI names the closed mismatch category and object identity without returning function/row contents

#### Scenario: Unavailable catalog is unknown
- **WHEN** connection or catalog inspection fails
- **THEN** the API/UI marks that source unavailable and does not count uninspected objects as matched
- **AND** separate retained definer and system diagnostics remain visible

#### Scenario: Observer uses only its exact permitted operations
- **WHEN** the dashboard control-plane observer reserves and records a probe
- **THEN** the constrained dashboard pool may invoke only its fixed declared observer functions/read projections, retaining epoch/sequence/CAS and owner-policy checks
- **AND** DashboardProbeRoleView and role=none ambient-owner authorization are removed without granting generic Switchboard or purpose-owner membership
