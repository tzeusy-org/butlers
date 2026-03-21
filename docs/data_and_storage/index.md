# Data and Storage

> **Scope:** How data is stored, migrated, and accessed.
> **Belongs here:** PostgreSQL topology, schema design, Alembic migrations, state store, blob storage, credential store.
> **Does NOT belong here:** Module-specific table details (see individual module pages).

- [Schema Topology](schema-topology.md) — shared vs per-butler schemas, PostgreSQL setup
- [Migration Patterns](migration-patterns.md) — Alembic conventions, module migration branching
- [State Store](state-store.md) — KV JSONB state store design
- [Blob Storage](blob-storage.md) — attachment and file storage
- [Credential Store](credential-store.md) — CLI auth token persistence
