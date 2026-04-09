## MODIFIED Requirements

### Requirement: Database Isolation Model
All butlers share a single PostgreSQL database (`butlers`) with per-butler schema isolation. The `public` schema provides cross-butler data access. Inter-butler communication is MCP-only through the Switchboard.

#### Scenario: Per-butler schema isolation (MODIFIED)
- **WHEN** a butler connects to the database
- **THEN** its asyncpg connection pool sets `server_settings = {"search_path": "{butler_schema},public"}`
- **AND** the pool's `setup` callback executes `SET ROLE "butler_{schema}_rw"` on every connection acquired from the pool
- **AND** PostgreSQL enforces that the butler can only write to its own schema and to specifically authorized public tables
- **AND** no butler can directly read or write another butler's schema
- **AND** asyncpg's built-in `RESET ALL` on connection return resets the role for pool safety

#### Scenario: SET ROLE graceful fallback (ADDED)
- **WHEN** a butler starts up and the runtime role (`butler_{schema}_rw`) does not exist in `pg_roles`
- **THEN** the butler logs a warning: "Role {role} not found; SET ROLE enforcement disabled"
- **AND** the connection pool is created without the `setup` callback
- **AND** the butler operates with the shared database user's privileges (same as pre-enforcement behavior)
- **AND** this fallback ensures development environments without CREATEROLE work without additional setup

#### Scenario: Public schema write authorization (ADDED)
- **WHEN** a butler operates under `SET ROLE` enforcement
- **THEN** it can INSERT, UPDATE, or DELETE rows in specifically authorized public tables (per the write authorization matrix in the database-security spec)
- **AND** it can SELECT from all public tables (unchanged from prior behavior)
- **AND** attempting to write to a public table not in the authorization matrix raises a PostgreSQL permission error

#### Scenario: Public schema (MODIFIED)
- **WHEN** cross-butler data is needed
- **THEN** it lives in the `public` schema (e.g., shared secrets, shared contacts, credential store)
- **AND** all butlers have read access to `public` via their search_path
- **AND** write access to specific `public` tables is granted by the `core_065` migration to all butler runtime roles
- **AND** the write authorization matrix is maintained in the `database-security` spec

#### Scenario: Dashboard API privileged access (ADDED)
- **WHEN** the dashboard API connects to the database
- **THEN** it uses the privileged shared database user without `SET ROLE`
- **AND** it intentionally has cross-schema read/write access for fan-out queries and aggregate views
- **AND** the `DatabaseManager` in `src/butlers/api/db.py` is not affected by SET ROLE enforcement

## Source References
- Non-Negotiable Rule 3 (MCP-only inter-butler communication; schema isolation is the DB-level enforcement of this rule)
- Non-Negotiable Rule 1 (User-federated; graceful fallback respects dev environment sovereignty)
- RFC 0006 (Database Schema and Isolation; SET ROLE enforcement transitions the advisory model to enforced)
- RFC 0010 (Briefing view grants are a sanctioned read-only cross-schema exception; SET ROLE does not affect them)
