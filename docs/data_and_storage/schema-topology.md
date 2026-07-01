# Schema Topology

> **Purpose:** Describe the single-database, multi-schema PostgreSQL topology that provides data isolation between butlers while sharing cross-cutting identity tables.
> **Audience:** Backend developers, DBAs, anyone deploying or extending butlers.
> **Prerequisites:** Familiarity with PostgreSQL schemas, asyncpg.

## Overview

![Schema Topology](./schema-topology.svg)

Butlers uses a **single PostgreSQL database** with **per-butler schemas** plus a `public` schema for cross-butler identity data. This topology replaced an earlier design where each butler had its own database. The migration target is one database named `butlers` with schema-based isolation.

## Database Layout

```
PostgreSQL Database: butlers
├── public          -- Extensions + cross-butler identity tables (entities, entity_info, …)
├── switchboard     -- Switchboard butler's private tables
├── general         -- General butler's private tables
├── relationship    -- Relationship butler's private tables
├── health          -- Health butler's private tables
└── <butler_name>   -- Any additional butler's private schema
```

### Cross-Butler Tables (in `public`)

The `public` schema contains tables that multiple butlers need to read. It is the canonical location for identity resolution data:

- **`public.entities`** -- Entity graph nodes. Each entity has a `canonical_name`, `entity_type`, `roles` array, and `metadata` JSONB.
- **`public.entity_info`** -- Key-value pairs attached to entities. Used for credential storage (e.g., `google_oauth_refresh` tokens). UNIQUE on `(entity_id, type)`.
- **`public.google_accounts`** -- Connected Google account registry with companion entities.
- **`public.memory_catalog`** -- Shared predicate/schema definitions for the memory module.

> **Note:** `public.contacts` and `public.contact_info` were dropped in migrations `core_115` / `core_134` and replaced by the entity graph above.

### Per-Butler Schemas

Each butler gets its own schema containing tables for:

- `state` -- KV JSONB state store (core)
- `scheduled_tasks` -- Cron-driven task definitions
- `sessions` -- Session log (append-only)
- `butler_secrets` -- Credential store table
- Module-specific tables (e.g., memory module's `episodes`, `facts`, `rules`)

## Schema Search Path

When a butler connects to the database, the `Database` class in `src/butlers/db.py` sets the PostgreSQL `search_path` to provide transparent name resolution:

```python
def schema_search_path(schema: str | None) -> str:
    # Returns: "<butler_schema>,shared,public"
```

For a butler named `general`, the search path is `general,public`. This means:

1. Unqualified table references resolve first to the butler's own schema.
2. If not found there, they resolve to `public` (identity tables).
3. Finally, `public` is checked (where PostgreSQL extensions like `vector` and `uuid-ossp` are installed).

This allows modules to reference `entities` without schema-qualifying it -- the search path resolves to `public.entities` automatically.

## Database Provisioning

### Pre-migration setup (superuser required)

Before running Alembic migrations on a fresh database, run
`scripts/init-db.sql` as a superuser or the database owner:

```bash
psql -h <host> -U <superuser> -d <dbname> -f scripts/init-db.sql
```

This script:

1. Installs required PostgreSQL extensions (`pgcrypto`, `uuid-ossp`, `vector`,
   `pg_trgm`).
2. Grants each butler runtime role (`butler_{schema}_rw` for all 10 schemas)
   and `connector_writer` to the connecting user (`POSTGRES_USER`, typically
   `butlers`).

**Why role membership matters:** Butler runtime code calls `SET ROLE
butler_{schema}_rw` before performing schema-isolated operations.  PostgreSQL
only permits `SET ROLE` to a role that the current user is a member of.
Without the grants in `init-db.sql`, all `SET ROLE` calls fail at runtime.

The `core_065` migration also grants membership via `GRANT role TO
CURRENT_USER`, which covers the migration-time user.  `init-db.sql` ensures
the same membership exists for the runtime connecting user, which may be
different.

### Runtime provisioning

The `Database` class handles provisioning at startup:

1. Connects to the `postgres` maintenance database.
2. Creates the target database if it does not exist (using `CREATE DATABASE ... TEMPLATE template0`).
3. Creates an asyncpg connection pool with `server_settings` that set the `search_path`.

The pool is configured with min/max size (default 2/10) and optional SSL mode support. SSL fallback logic handles environments where the server doesn't support STARTTLS gracefully.

## Connection Parameters

Connection parameters are resolved from environment in this order:

1. **`DATABASE_URL`** -- Full libpq-style URL (e.g., `postgres://user:pass@host:port/dbname?sslmode=require`)
2. **Individual `POSTGRES_*` variables** -- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_SSLMODE`

Defaults: `localhost:5432`, user `butlers`, password `butlers`.

## Pool Proxy Methods

The `Database` class exposes `fetch()`, `fetchrow()`, `fetchval()`, and `execute()` methods that proxy directly to the underlying asyncpg pool. Modules receive a `Database` instance and call these methods without needing direct pool access.

## Schema Isolation Guarantees

- Each butler can only see its own schema plus `public`.
- Inter-butler communication is MCP-only through the Switchboard -- no direct cross-schema SQL.
- The `public` schema is read-accessible by all butlers but write patterns are controlled by the core migration chain and identity resolution code.

## Verification

To confirm the schema topology described here matches the running system:

```bash
# 1. Per-butler schemas exist in the database
psql -h localhost -U butlers -d butlers -c "\dn"
# Expected: schemas listed include "public", "switchboard", "general",
#           "relationship", "health" (plus any other configured butlers)

# 2. Core tables exist in each butler's schema
psql -h localhost -U butlers -d butlers -c \
  "SELECT table_schema, table_name FROM information_schema.tables
   WHERE table_schema = 'general'
   ORDER BY table_name;"
# Expected: state, scheduled_tasks, sessions, butler_secrets,
#           plus module tables if modules are enabled

# 3. Cross-butler tables are in public schema
psql -h localhost -U butlers -d butlers -c \
  "SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'public' ORDER BY table_name;"
# Expected: entities, entity_info, google_accounts, model_catalog,
#           token_usage_ledger, model_dispatch_attempts, etc.
# Note: contacts and contact_info should NOT appear (dropped in core_115 / core_134)

# 4. Search path resolves butler-schema tables first, then public
# From a butler's connection context, unqualified references resolve correctly:
psql -h localhost -U butlers -d butlers \
  -c "SET search_path = general, public; SELECT COUNT(*) FROM state;"
# Expected: count from general.state, not an error

# 5. PostgreSQL extensions installed in public schema
psql -h localhost -U butlers -d butlers -c \
  "SELECT extname FROM pg_extension ORDER BY extname;"
# Expected: pgcrypto, uuid-ossp, vector, pg_trgm
```

## Related Pages

- [Migration Patterns](migration-patterns.md) -- How schema-scoped migrations work
- [State Store](state-store.md) -- The KV JSONB store within each butler schema
- [Credential Store](credential-store.md) -- Secret storage across schemas
