"""Add insight_candidates.prepared_action_id for the door binding.

Revision ID: core_225
Revises: core_224
Create Date: 2026-09-09 00:00:00.000000

bu-2jtfw.11: a prepared action (``pending_actions.origin='prepared'``,
per-butler-schema) is bound to the insight candidate that motivated it so the
digest can render a door ("approve"/"dismiss") for a live one and an honest
factual line for a terminal one, instead of a dead button. Nullable and
additive: existing candidates carry no prepared action and render exactly as
before.

No foreign key: ``pending_actions`` lives in each butler's own schema while
``insight_candidates`` lives in ``public`` (see broker.py's module docstring),
so the referenced row is not reachable via a same-database FK across the
schema-isolation boundary. ``origin_butler`` (already a NOT NULL column on
this table) plus this id together identify the referenced action; resolving
its live status is the caller's job (see core_226's narrow
SECURITY DEFINER lookup for the relationship-owned case).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_225"
down_revision = "core_224"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable prepared_action_id column."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.insight_candidates') IS NOT NULL THEN
                ALTER TABLE public.insight_candidates
                    ADD COLUMN IF NOT EXISTS prepared_action_id UUID;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Drop the prepared_action_id column."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.insight_candidates') IS NOT NULL THEN
                ALTER TABLE public.insight_candidates
                    DROP COLUMN IF EXISTS prepared_action_id;
            END IF;
        END $$;
        """
    )
