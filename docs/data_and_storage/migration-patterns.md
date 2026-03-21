# Migration Patterns

> **Purpose:** Explain how Alembic migrations are organized, discovered, and executed across core, module, and butler-specific chains.
> **Audience:** Developers adding new tables or modifying schema.
> **Prerequisites:** [Schema Topology](schema-topology.md), basic Alembic knowledge.

## Overview

Butlers uses Alembic for schema migrations with a **multi-chain branching model**. Instead of a single linear migration history, each migration domain (core infrastructure, individual modules, individual butlers) maintains its own independent revision chain. The daemon runs all applicable chains at startup via a programmatic API -- no CLI invocation required.

## Chain Types

### Core Chain

Location: `alembic/versions/core/`

The core chain manages shared infrastructure tables used by all butlers:

- `state` -- KV state store
- `scheduled_tasks` -- Cron scheduler
- `sessions` -- Session log
- `butler_secrets` -- Credential store
- `shared.contacts`, `shared.contact_info` -- Identity tables
- `shared.entities`, `shared.entity_info` -- Entity graph
- `shared.google_accounts` -- Google OAuth registry
- `ingestion_events` -- Switchboard ingestion log
- `model_catalog` -- LLM model definitions

Core migrations use the branch label `"core"` and revision IDs like `core_001`, `core_002`, etc. As of writing, the core chain has 38+ revisions.

### Module Chains

Location: `src/butlers/modules/<module_name>/migrations/`

Each module that needs its own tables maintains a migration chain within its source directory. The migration runner discovers these automatically by scanning `src/butlers/modules/*/migrations/` for directories containing `.py` files.

Example: the memory module at `src/butlers/modules/memory/migrations/` has 25+ revisions (branch label `"memory"`) creating tables like `episodes`, `facts`, `rules`, `entities`, `predicate_registry`, etc.

A module migration file follows this structure:

```python
"""memory_baseline"""
revision = "mem_001"
down_revision = None
branch_labels = ("memory",)
depends_on = None

def upgrade() -> None:
    op.execute("CREATE TABLE IF NOT EXISTS episodes (...)")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS episodes CASCADE")
```

Key conventions:
- `branch_labels` is set on the first revision in the chain (the root).
- `down_revision` chains within the module (e.g., `mem_002` has `down_revision = "mem_001"`).
- Migrations use raw SQL via `op.execute()` rather than Alembic's ORM-based operations.

### Butler-Specific Chains

Location: `roster/<butler_name>/migrations/`

Individual butlers can have their own migration chains for butler-specific tables. These are discovered by scanning `roster/*/migrations/` directories. Module chains take precedence if both exist for the same name.

## Discovery and Resolution

The `migrations.py` module (`src/butlers/migrations.py`) handles chain discovery:

1. **`_discover_module_chains()`** -- Scans `src/butlers/modules/*/migrations/` for Python files.
2. **`_discover_butler_chains()`** -- Scans `roster/*/migrations/` for Python files.
3. **`get_all_chains()`** -- Returns all chains in order: shared chains (`["core"]`) first, then module chains, then butler-specific chains. Duplicates are excluded.

The `_resolve_chain_dir()` function maps a chain name to its filesystem path:
- `"core"` resolves to `alembic/versions/core/`
- Module names resolve to `src/butlers/modules/<name>/migrations/`
- Butler names resolve to `roster/<name>/migrations/`

## Schema-Scoped Migration Execution

When running migrations, the target schema can be specified. This is critical for the multi-schema topology:

```python
await run_migrations(db_url, chain="all", schema="general")
```

When a schema is specified:
- Alembic's `version_table_schema` option is set so `alembic_version` tracking lives within the target schema.
- A custom `butlers.target_schema` option is passed through for migrations that need to create schema-qualified objects.

The `run_migrations()` function iterates through resolved chains and calls `command.upgrade(config, f"{chain}@head")` for each.

## Version Location Configuration

Alembic's `version_locations` setting is always configured with ALL known chain directories, regardless of which chain is being upgraded. This ensures Alembic can resolve every revision in `alembic_version` even when upgrading a single branch. Without this, cross-chain references would fail resolution.

## Migration Ordering at Startup

The butler daemon startup sequence runs migrations in this order:

1. Core migrations (`chain="core"`)
2. Module migrations (for each enabled module that returns a non-None `migration_revisions()`)
3. Butler-specific migrations (if `has_butler_chain(butler_name)` returns True)

Each chain is upgraded to its head independently. Alembic tracks which revisions have been applied per-chain in the `alembic_version` table within the target schema.

## Writing New Migrations

To add a migration to an existing module chain:

1. Create a new Python file in the module's `migrations/` directory.
2. Set `revision` to a unique ID (convention: `<prefix>_<number>`).
3. Set `down_revision` to the previous revision in the chain.
4. Do NOT set `branch_labels` (only the root revision has this).
5. Implement `upgrade()` and `downgrade()` using `op.execute()` with raw SQL.

To create a new module migration chain:

1. Create `src/butlers/modules/<name>/migrations/` directory.
2. Create `__init__.py` (empty).
3. Create the first migration with `branch_labels = ("<name>",)` and `down_revision = None`.
4. Return the branch label from the module's `migration_revisions()` method.

## Related Pages

- [Schema Topology](schema-topology.md) -- Database layout and search path
- [State Store](state-store.md) -- The `state` table created by core migrations
