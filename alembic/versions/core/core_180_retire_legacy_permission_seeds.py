"""Retire legacy core_121 permission default seed rows.

Revision ID: core_180
Revises: core_179
Create Date: 2026-07-23 00:00:00.000000

``core_121`` materialized inherited permission defaults as rows with the exact
``seeded default (core_121)`` reason. The matrix API now derives its complete
vocabulary independently and treats an absent row as inherited, so these rows
must be removed without interpreting or rewriting explicit operator choices.
"""

from __future__ import annotations

from alembic import op

revision = "core_180"
down_revision = "core_179"
branch_labels = None
depends_on = None


RETIRE_LEGACY_PERMISSION_SEEDS_SQL = """
DO $$
BEGIN
    IF to_regclass('public.permissions') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'permissions'
             AND column_name = 'reason'
       ) THEN
        RETURN;
    END IF;

    EXECUTE $delete_legacy_seeds$
        DELETE FROM public.permissions
        WHERE reason = 'seeded default (core_121)'
    $delete_legacy_seeds$;
END
$$;
"""


def upgrade() -> None:
    """Delete only the proven legacy defaults, safely across partial schemas."""
    op.execute(RETIRE_LEGACY_PERMISSION_SEEDS_SQL)


def downgrade() -> None:
    """Do not recreate rows that were never explicit operator choices."""
