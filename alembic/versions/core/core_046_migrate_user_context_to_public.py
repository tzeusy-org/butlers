"""migrate_user_context_to_public: move shared.user_context into the public schema.

Revision ID: core_046
Revises: core_045
Create Date: 2026-03-31 00:00:00.000000

Relocates ``shared.user_context`` into the ``public`` schema so that it sits
alongside other cross-butler tables (``public.contacts``, ``public.contact_info``,
etc.) rather than in a now-redundant ``shared`` schema.

Migration steps:

1. ALTER TABLE shared.user_context SET SCHEMA public
   Moves the table (including all indexes, constraints, and OIDs) in a single
   atomic operation.  The partial index ``idx_user_context_active_signals``
   moves automatically.

2. Verify the shared schema is empty.
   ``SELECT count(*) FROM information_schema.tables WHERE table_schema = 'shared'``
   is evaluated at upgrade time; if any tables remain we raise to avoid silent
   data loss.

3. DROP SCHEMA IF EXISTS shared CASCADE
   Removes the now-empty shared schema.

Design notes:
- ALTER TABLE SET SCHEMA is preferred over CREATE+INSERT+DROP because it
  preserves OIDs, indexes, constraints, sequences, and avoids a data copy.
- The downgrade path recreates the shared schema and reverses the SET SCHEMA
  operation.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_046"
down_revision = "core_045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # -------------------------------------------------------------------------
    # 1. Move shared.user_context to the public schema.
    # -------------------------------------------------------------------------
    op.execute("ALTER TABLE shared.user_context SET SCHEMA public")

    # -------------------------------------------------------------------------
    # 2. Verify the shared schema is now empty (safety guard).
    # -------------------------------------------------------------------------
    remaining = conn.execute(
        text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'shared'")
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"shared schema still contains {remaining} table(s) after migrating "
            "user_context; aborting DROP SCHEMA to prevent data loss."
        )

    # -------------------------------------------------------------------------
    # 3. Drop the now-empty shared schema.
    # -------------------------------------------------------------------------
    op.execute("DROP SCHEMA IF EXISTS shared CASCADE")


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Reverse: recreate the shared schema and move the table back.
    # -------------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS shared")
    op.execute("ALTER TABLE public.user_context SET SCHEMA shared")
