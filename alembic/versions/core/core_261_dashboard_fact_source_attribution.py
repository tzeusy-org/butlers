"""Private server-admitted dashboard source attribution; legacy remains unknown.

Revision ID: core_261
Revises: core_258

The common API table belongs to core, independently of Relationship startup.
This record is supplied only by the real OwnerAuth-admitted message producer.
It is neither an auth credential nor authority for a later HTTP request.
"""

from alembic import op

revision = "core_261"
down_revision = "core_258"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.dashboard_messages ADD COLUMN IF NOT EXISTS fact_owner_admission JSONB"
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM public.dashboard_messages
                    WHERE fact_owner_admission IS NOT NULL) THEN
            RAISE EXCEPTION 'source attribution exists; roll forward instead of erasing provenance';
          END IF;
        END $$;
        ALTER TABLE public.dashboard_messages DROP COLUMN fact_owner_admission;
    """)
