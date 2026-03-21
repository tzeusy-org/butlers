# Schema Topology

> **Purpose:** Describe the single-database, multi-schema PostgreSQL topology that provides data isolation between butlers while sharing cross-cutting identity tables.
> **Audience:** Backend developers, DBAs, anyone deploying or extending butlers.
> **Prerequisites:** Familiarity with PostgreSQL schemas, asyncpg.

## Overview

![Schema Topology](./schema-topology.svg)

Butlers uses a **single PostgreSQL database** with **per-butler schemas** plus a shared `shared` schema for cross-butler identity data. This topology replaced an earlier design where each butler had its own database. The migration target is one database named `butlers` with schema-based isolation.

## Database Layout

```
PostgreSQL Database: butlers
├── public          -- PostgreSQL default schema (extensions live here)
├── shared          -- Cross-butler identity tables (contacts, entities, secrets)
├── switchboard     -- Switchboard butler's private tables
├── general         -- General butler's private tables
├── relationship    -- Relationship butler's private tables
├── health          -- Health butler's private tables
└── <butler_name>   -- Any additional butler's private schema
```

### The `shared` Schema

The `shared` schema contains tables that multiple butlers need to read. It is the canonical location for identity resolution data:

- **`shared.contacts`** -- Canonical contact registry. One row per known person/actor. Includes a `roles` array (e.g., `['owner']`) and optional `entity_id` FK to the entity graph.
- **`shared.contact_info`** -- Per-channel identifiers linked to contacts (e.g., Telegram chat ID, email address). UNIQUE on `(type, value)`. `secured=true` marks credential entries.
- **`shared.entities`** -- Entity graph nodes. Each entity has a `tenant_id` (defaulting to `'shared'`), `canonical_name`, `entity_type`, `roles` array, and `metadata` JSONB.
- **`shared.entity_info`** -- Key-value pairs attached to entities. Used for credential storage (e.g., `google_oauth_refresh` tokens). UNIQUE on `(entity_id, type)`.
- **`shared.google_accounts`** -- Connected Google account registry with companion entities.
- **`shared.memory_catalog`** -- Shared predicate/schema definitions for the memory module.

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

For a butler named `general`, the search path is `general,shared,public`. This means:

1. Unqualified table references resolve first to the butler's own schema.
2. If not found there, they resolve to `shared` (identity tables).
3. Finally, `public` is checked (where PostgreSQL extensions like `vector` and `uuid-ossp` are installed).

This allows modules to reference `contacts` without schema-qualifying it -- the search path resolves to `shared.contacts` automatically.

## Database Provisioning

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

- Each butler can only see its own schema plus `shared` and `public`.
- Inter-butler communication is MCP-only through the Switchboard -- no direct cross-schema SQL.
- The `shared` schema is read-accessible by all butlers but write patterns are controlled by the core migration chain and identity resolution code.

## Related Pages

- [Migration Patterns](migration-patterns.md) -- How schema-scoped migrations work
- [State Store](state-store.md) -- The KV JSONB store within each butler schema
- [Credential Store](credential-store.md) -- Secret storage across schemas
