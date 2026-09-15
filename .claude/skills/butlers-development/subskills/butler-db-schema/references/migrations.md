# Alembic Migration System

Load when writing or reviewing a migration: chain architecture, the template,
conventions, adding a new butler's first migration, backward-compat rules,
schema-scoped execution, and core ACL. All schema changes go through Alembic —
no exceptions, no raw DDL in application code.

## Multi-Chain Architecture

Migrations are organized into independent chains auto-discovered by
`alembic/env.py`:

```
alembic/
  alembic.ini
  env.py                              # Multi-chain discovery + schema-scoped runner
  versions/
    core/                             # Shared core chain (branch_labels=("core",))
      core_001_target_state_baseline.py
      core_002_add_dispatch_mode_columns.py
      core_005_add_calendar_projection_tables.py
      ...

src/butlers/modules/
  memory/migrations/                  # Memory module chain (branch_labels=("memory",))
    001_memory_baseline.py
  approvals/migrations/               # Approvals module chain
    001_create_approvals_tables.py
    002_create_approval_events.py
  contacts/migrations/                # Contacts module chain
    001_contacts_sync_tables.py
  mailbox/migrations/                 # Mailbox module chain
    001_create_mailbox_table.py

roster/
  health/migrations/                  # Health butler chain (branch_labels=("health",))
    001_health_tables.py
  general/migrations/                 # General butler chain (branch_labels=("general",))
    001_general_tables.py
    002_add_entity_tags.py
  relationship/migrations/            # Relationship butler chain
    001_relationship_tables.py
    rel_002a_enrich_interactions.py
    ...
  messenger/migrations/               # Messenger butler chain
    msg_001_create_delivery_tables.py
  switchboard/migrations/             # Switchboard butler chain
    001_switchboard_tables.py
    002_extraction_tables.py
    ...
```

Discovery in `alembic/env.py`:
1. Shared chains: scans `alembic/versions/core/`
2. Module chains: scans `src/butlers/modules/*/migrations/`
3. Butler chains: scans `roster/*/migrations/`

## Migration Template

```python
"""<Short description of what this migration does>.

Revision ID: <prefix>_<number>
Revises:
Create Date: YYYY-MM-DD HH:MM:SS.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "<prefix>_001"      # e.g., "health_001", "mem_001", "core_005"
down_revision = None            # None for first migration in chain, else previous revision
branch_labels = ("<chain>",)    # Only on first migration in a chain (e.g., ("health",))
depends_on = None               # Cross-chain dependency (e.g., "core_001")


def upgrade() -> None:
    # Raw SQL via op.execute() — NO SQLAlchemy ORM operations
    op.execute("""
        CREATE TABLE IF NOT EXISTS example (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_example_name
        ON example (name)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_example_name")
    op.execute("DROP TABLE IF EXISTS example")
```

## Key Conventions

1. **Raw SQL only.** Use `op.execute("CREATE TABLE ...")`, not `op.create_table(...)` with SQLAlchemy Column objects. The project has `target_metadata=None`.
2. **`IF NOT EXISTS` / `IF EXISTS`.** All DDL uses idempotent forms because multiple schema-scoped runs execute the same migration file.
3. **Branch labels on first migration only.** The first migration in a chain sets `branch_labels = ("<chain_name>",)`. Subsequent migrations set `branch_labels = None`.
4. **Revision ID prefixes.** Use a chain prefix for readability:
   - Core: `core_001`, `core_002`, ...
   - Modules: `mem_001`, `approvals_001`, `contacts_001`, ...
   - Butlers: `health_001`, `gen_001`, `rel_001`, `msg_001`, `sw_001`, ...
5. **One logical change per migration.** Don't combine "add contacts table" and "add index on log" in the same migration.
6. **Always write `downgrade()`.** Even if you think you'll never roll back.
7. **No SQLAlchemy imports** beyond `from alembic import op`. Don't import `sa`, `sqlalchemy`, or `postgresql` dialect modules.

## Adding a New Butler's First Migration

1. Create `roster/<butler-name>/migrations/__init__.py` (empty file)
2. Create the migration file: `roster/<butler-name>/migrations/001_<butler>_tables.py`
3. Set `branch_labels = ("<butler-name>",)` on the first migration
4. Use the revision ID pattern: `<butler-name>_001`
5. The migration is auto-discovered by `alembic/env.py`

Example first migration for a new butler:

```python
"""create_finance_tables

Revision ID: finance_001
Revises:
Create Date: 2026-02-23 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "finance_001"
down_revision = None
branch_labels = ("finance",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            account_type TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            amount NUMERIC(12,2) NOT NULL,
            description TEXT,
            category TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_account_occurred
        ON transactions (account_id, occurred_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_category_occurred
        ON transactions (category, occurred_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS transactions")
    op.execute("DROP TABLE IF EXISTS accounts")
```

## Backward Compatibility Rules

**Every migration must be backward-compatible.** Assume the old code is still
running when the migration executes.

| Operation | Safe? | How to do it safely |
|---|---|---|
| Add a table | Yes | `CREATE TABLE IF NOT EXISTS`. Old code ignores it. |
| Add a nullable column | Yes | `ALTER TABLE ADD COLUMN ... DEFAULT NULL`. Old code ignores it. |
| Add a column with a default | Yes | `ALTER TABLE ADD COLUMN ... DEFAULT <value>`. Old code ignores it. |
| Add an index | Yes | Use `CREATE INDEX CONCURRENTLY` for large tables. See note below. |
| Drop a column | **Two-phase.** | Phase 1: Stop reading/writing the column in code. Deploy. Phase 2: Drop column. |
| Rename a column | **Two-phase.** | Phase 1: Add new column, backfill, update code. Phase 2: Drop old column. |
| Drop a table | **Two-phase.** | Phase 1: Remove all code references. Deploy. Phase 2: Drop table. |
| Change a column type | **Careful.** | Add new column, backfill, migrate code, drop old. |
| Add NOT NULL | **Two-phase.** | Phase 1: Backfill NULLs, set default in code. Phase 2: `SET NOT NULL`. |

**CONCURRENTLY note:** `CREATE INDEX CONCURRENTLY` cannot run inside a
transaction. If needed, the migration must disable the transaction wrapper:
```python
def upgrade() -> None:
    op.execute("COMMIT")  # Exit Alembic's transaction
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_name ON table (col)")
```

## Schema-Scoped Migration Execution

When the daemon runs migrations for a butler, `alembic/env.py` sets the target
schema:

```python
# In env.py run_migrations_online():
if target_schema is not None:
    connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {own_schema}")
    connection.exec_driver_sql(f"SET search_path TO {own_schema}, public")
```

This means:
- Core tables (`state`, `sessions`, etc.) are created **in the butler's own schema**, not in `public`
- Butler-specific tables are also created in the butler's own schema
- Each butler has its own copy of core tables (no cross-butler contamination)
- The `public` schema contains only tables explicitly created there by core migrations (calendar projections, etc.)

## Adding a New Butler to Core ACL

Each butler gets a runtime role (`butler_<name>_rw`) with:
- **Own schema:** SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES on tables; USAGE, SELECT, UPDATE on sequences
- **Shared schema:** SELECT only on tables; USAGE, SELECT on sequences
- **Other butler schemas:** All access REVOKED

These roles and privileges are managed by `core_001_target_state_baseline.py`.
When adding a new butler, `core_001` (or a subsequent core migration) must list
the butler in `_BUTLER_SCHEMAS` to create its runtime role and grant
privileges. If the butler is added after the initial deployment, write a new
core migration that:

1. Creates the schema
2. Creates the runtime role
3. Grants appropriate privileges
