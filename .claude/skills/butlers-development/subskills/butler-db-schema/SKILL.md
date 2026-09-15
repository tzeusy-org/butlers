---
name: butler-db-schema
description: Guide for designing and managing a butler's PostgreSQL database schema. Use when creating tables, writing migrations, adding indexes, or evolving a butler's data model.
metadata:
  owner: tze
  authors: [tze, Claude]
  status: active
  last_reviewed: "2026-09-05"
---

# Butler Database Schema Design

Use this skill when creating or modifying a butler's database schema — adding
tables, writing Alembic migrations, designing indexes, or evolving the data
model for a specific butler's needs.

## When to load which reference

| You need | Load |
|---|---|
| Exact DDL of the five core tables (`state`, `sessions`, `scheduled_tasks`, `route_inbox`, `butler_secrets`) | [`references/core-tables.md`](references/core-tables.md) |
| Shared `public` tables, module chains (memory/approvals/contacts), butler-specific domain tables, schema-design principles | [`references/shared-and-module-tables.md`](references/shared-and-module-tables.md) |
| Index types, when to use each, naming convention | [`references/indexing.md`](references/indexing.md) |
| Writing/reviewing a migration: chains, template, conventions, new-butler first migration, backward-compat rules, schema-scoped execution, core ACL | [`references/migrations.md`](references/migrations.md) |

## Hard Constraints

- **Shared database, per-butler schemas.** All butlers share a single PostgreSQL database named `butlers`. Each butler gets its own schema (`general`, `health`, `messenger`, etc.) plus read access to `public`. Inter-butler data exchange happens only via MCP tools through the Switchboard.
- **Five core tables in every butler schema.** `state`, `sessions`, `scheduled_tasks`, `route_inbox`, `butler_secrets` — all created by `core_001_target_state_baseline.py` and replicated into each butler's schema via `search_path`. DDL in [`references/core-tables.md`](references/core-tables.md).
- **Migrations via Alembic only.** No raw DDL in application code. No "just run this SQL."
- **Raw SQL via `op.execute()`.** Migrations use raw SQL strings, not SQLAlchemy ORM operations. There are no SQLAlchemy models (`target_metadata=None`).
- **Backward compatibility in all migrations.** Every migration must be safe to run while the previous version of the code is still active.

## Database Topology

```
PostgreSQL database: "butlers"
├── public          # Extensions (pgcrypto, vector, uuid-ossp) + cross-butler tables (identity, model catalog, calendar projections)
├── general         # General butler's domain tables
├── health          # Health butler's domain tables
├── messenger       # Messenger butler's domain tables
├── relationship    # Relationship butler's domain tables
└── switchboard     # Switchboard butler's domain tables
```

Each butler's runtime connection sets `SET search_path TO <own_schema>, public`,
so its tools query `state`, `sessions`, etc. and `public` tables (like
`calendar_sources`) without schema-qualifying. Each butler also gets a runtime
role (`butler_<name>_rw`) — read/write on its own schema, read-only on `public`,
all access to other butler schemas REVOKED. Roles and the `_BUTLER_SCHEMAS`
allowlist are managed in core migrations; see [`references/migrations.md`](references/migrations.md).

## Schema Design Principles

Applies to every domain table you add (details + module/butler examples in
[`references/shared-and-module-tables.md`](references/shared-and-module-tables.md)):

1. **JSONB for flexible/evolving fields.** Typed columns for what you query on (FKs, timestamps, amounts); JSONB for metadata, details, and fields that vary across records.
2. **Always include `created_at`** (`TIMESTAMPTZ NOT NULL DEFAULT now()`); add `updated_at` on mutable tables.
3. **`UUID` primary keys** for domain tables; `BIGINT GENERATED ALWAYS AS IDENTITY` only for high-volume append-only tables.
4. **`TEXT` over `VARCHAR`** (identical in Postgres, simpler).
5. **JSONB arrays for tags** (`JSONB DEFAULT '[]'::jsonb`) over `TEXT[]` — established pattern.
6. **Cascade deletes where ownership is clear** (`ON DELETE CASCADE` for child records meaningless without their parent).
7. **CHECK constraints for enums** (`CHECK (status IN ('pending', 'active', 'done'))`) instead of a lookup table.

Indexing follows recency bias — see [`references/indexing.md`](references/indexing.md).

## What NOT to Do

- **Don't use SQLAlchemy ORM** in migrations — use `op.execute()` with raw SQL.
- **Don't create separate databases** — all butlers share `butlers` DB with schema isolation.
- **Don't access other butler schemas directly** — use MCP/Switchboard for inter-butler communication.
- **Don't skip `IF NOT EXISTS`** — migrations run per-schema, idempotency is required.
- **Don't use `datetime.now()` in SQL** — use `now()` for consistency.
- **Don't put migrations in `src/butlers/db/`** — they go in `alembic/versions/core/`, `src/butlers/modules/*/migrations/`, or `roster/*/migrations/`.
- **Don't import `sqlalchemy` in migrations** — only import `from alembic import op`.
