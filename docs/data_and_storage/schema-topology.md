# Schema Topology

> **Purpose:** Describe the single-database, multi-schema PostgreSQL topology that provides data isolation between butlers while sharing cross-cutting identity tables.
> **Audience:** Backend developers, DBAs, anyone deploying or extending butlers.
> **Prerequisites:** Familiarity with PostgreSQL schemas, asyncpg.

## Overview

![Schema Topology](./schema-topology.svg)

Butlers uses a **single PostgreSQL database** with **per-butler schemas** plus a `public` schema for cross-butler identity data. The database is named `butlers`.

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
- **`public.memory_catalog`** -- Cross-butler discovery index over memory items: a searchable
  summary plus provenance pointers back to the owning butler's schema. It is not a canonical store;
  full recall routes back to the owning schema.
- **`public.qa_publication_operations`** -- Durable QA publication operation ledger (core_266; core_267 adds forward-only stage, immutable binding and exact reconciliation; core_268 adds set-once `new_object_id` and set-once `result` (checked after the stage-forward rule), a result/stage CHECK and a held-row delete guard):
  one row per attempt and closed operation kind, with binding/payload digests, stage and result.
  Only `butler_qa_rw` writes (forced RLS); every runtime role reads. An unresolved row holds its
  fingerprint lineage. See [QA Publication Boundary](../operations/qa-publication-boundary.md).
- **`public.butler_secrets`** -- Shared credential authority installed by the
  explicit migrator. Its dedicated credential-writer pool is distinct from the
  connector capability; daemon boot performs no credential-table DDL.

### Per-Butler Schemas

Each butler gets its own schema containing tables for:

- `state` -- KV JSONB state store (core)
- `scheduled_tasks` -- Cron-driven task definitions
- `sessions` -- Session log (append-only)
- `butler_secrets` -- Credential store table
- Module-specific tables (e.g., memory module's `episodes`, `facts`, `rules`)

## Schema Search Path

When a butler connects, the `Database` class in `src/butlers/db.py` sets `search_path` from
`schema_search_path()`: the butler's own schema, then `public`. For `general` that is
`general,public`. Unqualified names resolve to the butler's own tables first, then to the
cross-butler tables and extensions in `public`. That is how a module can reference `entities`
without qualifying it.

## Database Provisioning

### Explicit bootstrap and migration

The operator provisions the normal migration owner and the separate serving
logins/secret references. The explicit `butlers db bootstrap` producer compiles
the source deployment manifest and executes `scripts/init-db.sql` on its
selected privileged maintenance connection. The script refuses an absent or
ambiguous manifest. It prepares extensions, source roles and schemas; it does
not grant serving membership to the normal owner, create runtime passwords or
install broad table/default privileges.

`butlers db migrate` retains one maintenance session and advisory lock across
all declared chains and exact finalization. Ordinary DDL runs as the independently
observed normal owner. Controlled protected installation uses only the explicit
maintenance prerequisite. The final manifest records each object/signature,
owner, operation, column and capability. Missing prerequisites or unlisted
objects refuse instead of producing an inferred grant.

### Read-only runtime admission

`Database.connect()` opens the configured existing database, selects the exact
source-derived runtime role and validates the real session login's recursive
role reach, attributes, ownership, CREATE reach and object catalog before
publishing a pool. Every acquisition repeats admission after role reset.
Serving startup cannot create databases, run migrations or repair grants.
An authorized isolated administrative provisioning helper remains separate
from this serving path.

The pool size comes from `BUTLERS_DB_POOL_MIN_SIZE` / `BUTLERS_DB_POOL_MAX_SIZE` (default 1/10; see
`pool_sizes_from_env` in `db.py`), with optional SSL mode support. SSL fallback logic handles environments where the server doesn't support STARTTLS gracefully.

## Connection Parameters

Connection parameters are resolved from environment in this order:

1. **`DATABASE_URL`** -- Full libpq-style URL (e.g., `postgres://user:pass@host:port/dbname?sslmode=require`)
2. **Individual `POSTGRES_*` variables** -- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_SSLMODE`

Transport defaults do not establish authority. A serving login must still have
exactly its source-selected capability reach and satisfy current catalog
admission. Dashboard, connector, credential, owner-auth and migration consumers
select their distinct explicit credential references; a missing reference never
falls back to a shared owner or migration login.

## Pool Proxy Methods

The `Database` class exposes `fetch()`, `fetchrow()`, `fetchval()`, and `execute()` methods that proxy directly to the underlying asyncpg pool. Modules receive a `Database` instance and call these methods without needing direct pool access.

## Schema Isolation Guarantees

- Each butler's runtime has its declared primary/private schemas plus only the exact additional reads and operations named by the adopted source manifest. Named Chronicler evidence readers and other adopted cross-schema projections are explicit exceptions; discovery cannot extend them.
- Inter-butler actions go through MCP and the Switchboard. Direct cross-schema SQL is limited to the exact adopted readers/projections in the source manifest; it is not a general action or ownership channel.
- `public` access is defined per object, operation and role, with the required RLS policies retained. An unlisted public object has no ambient runtime read or write grant.

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

## Implementation Notes

- With `name = "butlers"`, `[butler.db]` requires an explicit primary schema. Serving `Database` profiles bind the actual declared database, primary/private ownership and dedicated login reach; a random database or search path cannot manufacture a serving profile.
- Runtime, dashboard, connector, credential, migration and bootstrap consumers use their distinct declared purpose profiles. Missing, privileged or cross-purpose membership refuses before a usable pool. Owner authentication remains independently provisioned.
- `scripts/init-db.sql` prepares the explicit source role/schema/extension manifest on an authorized administrative connection. The held migrator then applies ordinary DDL as the normal owner and installs/converges exact final permissions. Neither bootstrap reruns nor core replay grant broad runtime/default public DML; catalog discovery cannot add authority.
- A permission failure requires diagnosing the actual dedicated login, complete role reach and source catalog. Do not repair it by falling back to the normal owner, adding caller membership or replaying a predecessor initializer against the rebuilt serving state. The pinned predecessor initializer is reserved for the explicit preserving operator rollback.
- Forced RLS, append-only evidence and narrow cross-schema readers remain mandatory independently of table ACLs. Backup and restore use their exact selected purpose; inaccessible objects must be handled through the supported backup policy, never by disabling RLS or widening serving grants.
- DND gateway/private writer, guard, audit and policies retain their fixed `dnd_generation_owner` NOLOGIN/nonprivileged ownership, forced RLS and General/Switchboard-only invocation. The private mutation body uses `search_path = pg_catalog, pg_temp`, qualified built-in `sha256()` and exact replay/current-generation fences. The source-owned finalizer installs that body; the retired `dnd_generation_admin` namespace is not an operative repair interface. Read-only catalog diagnostics distinguish unavailable, missing and mismatch without exposing function bodies or row contents.
- One `permission denied` in `pg_dump`'s lock sweep aborts the whole dump, so derive the exclusion
  list from all three fences as the dump role (no schema `USAGE`, no table `SELECT`, forced RLS or
  RLS on a table it does not own), not from the last error. Never use `--enable-row-security`: it
  dumps only visible rows and yields a backup that looks complete.

## Related Pages

- [Migration Patterns](migration-patterns.md) -- How schema-scoped migrations work
- [State Store](state-store.md) -- The KV JSONB store within each butler schema
- [Credential Store](credential-store.md) -- Secret storage across schemas

## Relationship report attribution topology

`rel_037` is additive after immutable `rel_036`: it owns fact report/confirmation columns,
candidate occurrence uniqueness, private approval lineage and exact identity-decision receipts.
It does not alter the effective-time transition indexes, reserve the held cutover successor,
add a global grant, or change cross-butler schema isolation. Downgrade refuses classified data
instead of erasing provenance. The canonical Relationship resolver/writer remain on the
Relationship runtime role; Switchboard uses their registered MCP interface.

`core_261` separately owns the nullable private `public.dashboard_messages.fact_owner_admission`
field, because dashboard conversations work without Relationship startup. The actual admitted
API producer stamps a new user message and binds its normalized accepted content. Old messages
and core-only installations below this additive revision stay unstamped; retries preserve that
unknown state. A caller's dashboard label, actor, status or boolean is not this producer witness.
The shared public-table grants and trusted-host premise remain unchanged; the stamp is not a
credential or protection against a compromised trusted database actor.

## Dark Switchboard goal ledger

`sw_047` adds `switchboard.{goals,invocations,effects,budgets,admission_decisions,goal_inbox}`
for RFC0038's unwired admission slice. `core_273` adds a nullable explicit
`runtime_config.goal_admission_config` snapshot in each migrated core schema;
only Switchboard's snapshot is consumed. These ordinary owner-held tables enable
non-FORCE RLS for the exact Switchboard runtime, preserving migration-owner full
backup while denying other runtimes after bootstrap regrants. See
[goal ledger and admission](../architecture/goal-ledger-and-admission.md) for
source keys, lock order, policy snapshots and actual verification obligations.

## Database-authority source and deployment boundary

This source implements the adopted successor to shared ownership and ambient
grants. Source/software controls do not establish actual PostgreSQL acceptance
or a completed deployment. See [the cutover procedure](../identity_and_secrets/database-authority-cutover.md)
for the complete proof and operator boundary; do not operate a mixed model.

The target has three connecting profiles: CLI-only migrator, cooperative runtime with exactly derived roster capabilities, and a constrained dashboard capability. Dedicated owner-auth remains independent. The migrator owns ordinary objects; unreachable NOLOGIN purpose owners retain fenced state and definers. Recursive reachable ownership, not only current_user after SET ROLE, is admission evidence. Actual private Memory schemas and current same-database JSONB/search_path/SSL behavior remain.

One exact manifest records every managed public object/signature, owner, role operation/column, PUBLIC/default ACL, RLS, producer and selected pool. Unknown objects get no default SELECT or DML. Runtime own-schema access does not imply access to another schema. General briefing, QA policy projection, Concierge telemetry and named message_inbox reads retain only their adopted scopes. The current per-butler schema layout does not change, and retired table names do not become new shared objects.
