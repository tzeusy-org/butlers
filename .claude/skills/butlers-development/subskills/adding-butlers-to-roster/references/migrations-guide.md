# Alembic Migrations Guide

Only needed if the butler persists data. Module migrations (calendar, contacts,
memory) are handled by the module system in `src/butlers/modules/<module>/migrations/`
— don't duplicate them here.

## File structure

```
migrations/
├── __init__.py                    # Empty file (required)
└── 001_<butler-name>_tables.py    # First migration
```

## Template

```python
"""create_<butler_name>_tables

Revision ID: <butler>_001
Revises:
Create Date: <date>

"""

from __future__ import annotations

from alembic import op

revision = "<butler>_001"
down_revision = None
branch_labels = ("<butler-name>",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS <table_name> (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- domain columns here
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_<table>_<col>
            ON <table_name> (<col>)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS <table_name>")
```

## Critical rules

- `branch_labels` MUST be a tuple with the butler name: `("<butler-name>",)`. Enables per-butler migration chains.
- Revision naming: `<butler>_001` format (e.g., `health_001`, `rel_001`).
- First migration: `down_revision = None`. Subsequent: `down_revision = "<butler>_001"`.
- Multiple revisions at the same level are allowed when independent (e.g., `rel_002a`, `rel_002c`).
- Use `op.execute()` with raw SQL, not SQLAlchemy ORM operations.
- Always include `IF NOT EXISTS` / `IF EXISTS` guards.
- Add GIN indexes on JSONB columns for containment queries (`@>`).
- UUID primary keys with `gen_random_uuid()`.
- Use `TIMESTAMPTZ` (not `TIMESTAMP`) for all datetime columns.
