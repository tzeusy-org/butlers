# PostgreSQL Storage And Migrations

Estimated smart-human study time: 9 hours

## Why This Module Matters

Most persistent behavior in Butlers is shaped by PostgreSQL schema isolation, shared `public` tables, JSONB payloads, asyncpg behavior, role grants, and Alembic migration chains. A safe contributor needs database fundamentals before modifying data paths.

## Learning Goals

- Explain one-database/multi-schema isolation and `search_path`.
- Understand roles, runtime grants, and why direct cross-schema access is exceptional.
- Reason about JSONB storage and application-layer validation.
- Write or review guarded, chain-safe Alembic migrations.

## Subsection: Schemas, Roles, And `search_path`

### Why This Matters Here

Each butler has its own schema while shared identity, model catalog, credentials, and settings live in `public`. SQL that works in one schema may be unsafe or wrong in another.

### Technical Deep Dive

A PostgreSQL schema is a namespace inside a database. `search_path` decides how unqualified table names resolve. If a connection sets `search_path` to `relationship,public`, then `SELECT * FROM sessions` finds `relationship.sessions` before any shared table.

Roles control permissions. A migration user may own schemas and create objects, while runtime roles should have only the privileges needed to run. `SET ROLE` changes effective permissions. Default privileges matter because future tables need grants too.

This model gives isolation without separate databases, but it requires discipline: avoid accidental cross-schema reads, qualify exceptional shared access, and understand which pool/role is active.

### Where It Appears In The Repo

- `docs/architecture/database-design.md`
- `docs/data_and_storage/schema-topology.md`
- `src/butlers/db.py`
- `scripts/init-db.sql`
- `tests/integration/test_schema_isolation.py`

### Sample Q&A

- Q: Why can unqualified SQL be acceptable in this repo?
  A: Because the connection pool sets a schema-specific `search_path`; unqualified names resolve to the current butler schema first.
- Q: Why are cross-butler foreign keys avoided?
  A: They would couple isolated schemas and undermine the routing/tool boundary.

### Progress

- [ ] Exposed: I can define schema, role, grant, `search_path`, and `SET ROLE`.
- [ ] Working: I can predict which schema an unqualified query targets.
- [ ] Contribution-ready: I can explain why a proposed cross-schema query is safe or unsafe.

### Mastery Check

Target level: `contribution-ready`

You should be able to inspect a SQL query and identify the intended schema, role assumptions, and any cross-boundary risk.

## Subsection: JSONB, Serialization, And Validation

### Why This Matters Here

State values, tool calls, route envelopes, scheduler results, job args, and triage conditions use JSONB. Some asyncpg paths can round-trip JSONB as dicts or strings, so assumptions about representation matter.

### Technical Deep Dive

JSONB is flexible structured storage. It is useful when payloads evolve faster than table columns or when each row can carry a different shape. Flexibility moves schema discipline upward: application code must validate payloads, normalize input, and decide which fields deserve real columns or indexes.

Serialization boundaries matter. A Python dict, a JSON string, and a PostgreSQL JSONB value are not the same thing. Good code normalizes at boundaries: before writing, before comparing, and after reading. Indexes can make JSONB queryable, but only for deliberate access patterns.

### Where It Appears In The Repo

- `docs/architecture/database-design.md`
- `docs/data_and_storage/state-store.md`
- `src/butlers/core/scheduler.py`
- `src/butlers/modules/approvals/models.py`
- `tests/modules/test_calendar_reminder_integration.py`

### Sample Q&A

- Q: Why not store every flexible payload as raw text?
  A: JSONB preserves structure, enables validation and selected indexing, and avoids ad hoc string parsing.
- Q: Why normalize JSONB reads before diffing?
  A: The same logical value may arrive as a dict or JSON string depending on driver/code path.

### Progress

- [ ] Exposed: I can define JSONB, serialization, normalization, and application-layer validation.
- [ ] Working: I can explain when JSONB is appropriate versus a typed column.
- [ ] Contribution-ready: I can identify a read/write boundary that needs explicit normalization.

### Mastery Check

Target level: `contribution-ready`

You should be able to review a persisted JSON payload and explain its validation, indexing, and compatibility implications.

## Subsection: Alembic Chain-Safe Migrations

### Why This Matters Here

The repo has core migrations, module migrations, and roster-specific migrations. Optional schemas and partial deployments mean migrations often need guards.

### Technical Deep Dive

Alembic migrations are versioned database changes. In a multi-chain system, each chain must stay linear and uniquely identified. Branch labels, revision IDs, version locations, and upgrade ordering all matter.

Guarded DDL checks whether a table, schema, index, or column exists before operating on it. This is important when a core migration touches optional module tables or when fresh databases start from a different baseline than upgraded ones. Migration tests should verify not only file naming but that Alembic can load and apply the chain.

### Where It Appears In The Repo

- `docs/data_and_storage/migration-patterns.md`
- `src/butlers/migrations.py`
- `alembic/versions/core/`
- `src/butlers/modules/*/migrations/`
- `roster/*/migrations/`
- `tests/config/test_migrations.py`

### Sample Q&A

- Q: Why should a core migration guard references to optional module tables?
  A: Fresh or core-only databases may not have those tables, and the migration should still succeed.
- Q: What is the risk of duplicate revision IDs?
  A: Alembic can load multiple heads or ambiguous revisions and fail before applying migrations.

### Progress

- [ ] Exposed: I can define revision, branch label, migration chain, head, and guarded DDL.
- [ ] Working: I can explain the difference between core, module, and roster migration chains.
- [ ] Contribution-ready: I can review a migration for optional-schema and multi-chain hazards.

### Mastery Check

Target level: `contribution-ready`

You should be able to design a small schema change, place it in the correct chain, guard optional dependencies, and choose the migration tests to run.

## Module Mastery Gate

- [ ] I can explain the repo's one-db/multi-schema storage model.
- [ ] I can reason about JSONB serialization and validation boundaries.
- [ ] I can identify the correct migration chain for a table change.
- [ ] I can name at least three migration failure modes this repo tests for.
