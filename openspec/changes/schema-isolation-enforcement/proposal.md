## Why

Butler schema isolation is currently advisory. RFC 0006 defines per-butler PostgreSQL roles (`butler_{name}_rw`) with proper ACLs -- own schema full RW, public schema SELECT only, other schemas explicitly revoked -- and the `core_001_foundation` migration creates these roles and grants. But at runtime, every butler connects as the shared `butlers` database user with only `search_path` isolation. The roles exist in `pg_roles` but are never assumed. A bug in one butler, a malformed query, or a compromised LLM session could read or write any schema in the database.

This is explicitly called out in RFC 0006 section "Staffer Schema Permissions and Cross-Butler Access": "In v1 this model is advisory: violations are flagged in logs but not enforced at the database or network level." The declarative model was designed to support database-level enforcement without schema changes. The roles are there. The grants are there. We just need to use them.

The secondary problem is that `core_001_foundation` grants only `SELECT` on all public tables to butler roles. At runtime, butlers write to many public tables (identity, ingestion, QA, model routing, memory catalog, etc.). Turning on `SET ROLE` without adding targeted write grants would break every butler immediately.

## What Changes

- **Amend RFC 0006** to transition the isolation model from "advisory" to "enforced" via `SET ROLE` on connection acquire.
- **Add a core migration** (`core_065_public_schema_write_grants`) that grants targeted `INSERT`, `UPDATE`, `DELETE` privileges on specific public tables to all butler runtime roles. The write authorization matrix is derived from a complete audit of all public table writes across modules, connectors, and core infrastructure.
- **Modify `Database.connect()`** to accept a `role` parameter and pass an asyncpg `setup` callback that runs `SET ROLE "role_name"` on every connection acquired from the pool. asyncpg's `RESET ALL` on connection return handles cleanup.
- **Wire the role** in `lifecycle.py` where `Database.from_env()` is called, deriving the role name from the butler's schema name using the existing `butler_{schema}_rw` convention.
- **Handle connectors** by extending the `connector_writer` role with matching public write grants and wiring `SET ROLE` in connector pool creation.
- **Graceful fallback** when the role does not exist (dev environments without CREATEROLE): log a warning and skip `SET ROLE`, preserving current behavior.
- **Update contract and integration tests** to validate role enforcement.

## Capabilities

### Modified Capabilities
- `butler-base-spec`: Update the "Database Isolation Model" requirement to document `SET ROLE` enforcement, the `role` parameter on `Database`, and the graceful fallback behavior.
- `database-security`: New spec documenting the role enforcement model, write authorization matrix, connector role enforcement, and the fallback policy.

## Impact

- **Database module** (`src/butlers/db.py`): New `role` parameter on `Database.__init__`, `_setup_connection()` method, modified `connect()` to pass `setup` callback to `asyncpg.create_pool()`.
- **Lifecycle** (`src/butlers/lifecycle.py`): Derive role name from schema, pass to `Database` constructor.
- **Migration** (`alembic/versions/core/core_065_public_schema_write_grants.py`): Targeted GRANT statements for 21 public tables.
- **Connector pools**: Each connector's `asyncpg.create_pool()` call needs a `setup` callback for `SET ROLE connector_writer`.
- **Dashboard API** (`src/butlers/api/db.py`): NOT affected -- uses separate privileged pools that intentionally need cross-schema access.
- **Tests**: New contract tests for role enforcement; updated integration tests for public write grants.
