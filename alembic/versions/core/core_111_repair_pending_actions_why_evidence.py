"""pending_actions: repair why/evidence columns for late-created tables.

Revision ID: core_111
Revises: core_110
Create Date: 2026-05-25 00:00:00.000000

``core_097`` added ``why`` and ``evidence`` to every butler schema that already
had a ``pending_actions`` table.  Some module-managed approval tables can be
created after that core migration has run, leaving the table without the columns
that newer approval writers use.

This migration is an idempotent repair pass over all non-system
``pending_actions`` tables.  It also covers legacy single-schema deployments
where the table lives in ``public``.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_111"
down_revision = "core_110"
branch_labels = None
depends_on = None


def _pending_action_schemas() -> list[str]:
    """Return schemas that currently contain a pending_actions table."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT DISTINCT n.nspname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'pending_actions'
              AND c.relkind = 'r'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY n.nspname
            """
        )
    ).fetchall()
    return [row[0] for row in rows]


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def upgrade() -> None:
    for schema in _pending_action_schemas():
        table_fqn = f"{_quote_ident(schema)}.pending_actions"
        op.execute(
            f"""
            ALTER TABLE {table_fqn}
                ADD COLUMN IF NOT EXISTS why TEXT,
                ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb
            """
        )


def downgrade() -> None:
    # Repair-only migration: these columns are now part of the base schema and
    # may already contain data in healthy deployments.
    pass
