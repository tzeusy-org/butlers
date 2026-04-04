"""healing_attempts_qa_patrol_id: add qa_patrol_id FK to public.healing_attempts.

Revision ID: core_054
Revises: core_053
Create Date: 2026-04-05 00:00:00.000000

Adds a nullable ``qa_patrol_id`` column to ``public.healing_attempts`` to
support QA-originated investigations. Rows inserted by the per-butler
self-healing module have ``qa_patrol_id = NULL`` (existing behaviour is
preserved). Rows inserted by the QA staffer have ``qa_patrol_id`` set to the
originating patrol's ID.

Strategy (avoids long table locks):
  1. ADD COLUMN … DEFAULT NULL  — instant metadata change, no row rewrite.
  2. ADD CONSTRAINT … NOT VALID  — validates new/updated rows only, does not
     scan existing rows (avoids AccessExclusiveLock on the full table).
  3. VALIDATE CONSTRAINT  — scans existing rows, uses ShareUpdateExclusiveLock
     (allows concurrent reads/writes during the scan).

See: https://www.postgresql.org/docs/current/sql-altertable.html#SQL-ALTERTABLE-NOTES

The column references public.qa_patrols which was created in core_051.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_054"
down_revision = "core_053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Add the column with DEFAULT NULL — instant, no row rewrite.
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS qa_patrol_id UUID DEFAULT NULL
    """)

    # Step 2: Add FK constraint NOT VALID — only validates new/updated rows,
    # avoids a full table scan at migration time (safe for large tables).
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_healing_attempts_qa_patrol_id'
                  AND conrelid = 'public.healing_attempts'::regclass
            ) THEN
                ALTER TABLE public.healing_attempts
                    ADD CONSTRAINT fk_healing_attempts_qa_patrol_id
                    FOREIGN KEY (qa_patrol_id)
                    REFERENCES public.qa_patrols(id)
                    ON DELETE SET NULL
                    NOT VALID;
            END IF;
        END
        $$;
    """)

    # Step 3: Validate the constraint against existing rows.
    # Uses ShareUpdateExclusiveLock — allows concurrent reads/writes.
    op.execute("""
        ALTER TABLE public.healing_attempts
            VALIDATE CONSTRAINT fk_healing_attempts_qa_patrol_id
    """)

    # Index to support joins from qa_patrols → healing_attempts.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_healing_attempts_qa_patrol_id
        ON public.healing_attempts (qa_patrol_id)
        WHERE qa_patrol_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS public.idx_healing_attempts_qa_patrol_id
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP CONSTRAINT IF EXISTS fk_healing_attempts_qa_patrol_id
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS qa_patrol_id
    """)
