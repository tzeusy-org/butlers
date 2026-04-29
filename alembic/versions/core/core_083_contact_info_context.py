"""contact_info_context: add context column to public.contact_info.

Revision ID: core_083
Revises: core_082
Create Date: 2026-04-29 00:00:00.000000

Adds a ``context`` column (VARCHAR, nullable) to ``public.contact_info`` to
tag each channel identifier as belonging to a personal, work, or other sphere.

Allowed values: ``personal | work | other | NULL``.
NULL means "unclassified / unknown" and is treated the same as ``personal``
in the context-aware recipient resolution path (fail-safe default).

Backfill: existing rows are left as NULL (equivalent to personal in runtime
logic) — we deliberately do NOT blindly write ``personal`` to avoid
masking any future distinction. The migration documents that NULL == personal
for routing purposes. Operators may update rows explicitly via the dashboard.

This migration does NOT flip any rows to ``work`` automatically. Work-domain
heuristic detection is deferred to a follow-up bead.

Index added: ``idx_contact_info_context`` on ``(context)`` for filtered
recipient resolution queries.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_083"
down_revision = "core_082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add context column to public.contact_info
    op.execute("""
        ALTER TABLE public.contact_info
        ADD COLUMN IF NOT EXISTS context VARCHAR
        CHECK (context IN ('personal', 'work', 'other'))
    """)

    # Create index on context for efficient filtering
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'contact_info'
                  AND indexname = 'idx_contact_info_context'
            ) THEN
                CREATE INDEX idx_contact_info_context
                ON public.contact_info (context)
                WHERE context IS NOT NULL;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_contact_info_context")
    op.execute("ALTER TABLE public.contact_info DROP COLUMN IF EXISTS context")
