"""dashboard_conversations_routed_butler: sticky routing target column.

Revision ID: core_153
Revises: core_152
Create Date: 2026-07-05 00:00:00.000000

Backs the owner chat widget's sticky-follow-up mechanic (bu-p6ey8.1): a
classification-routed (Switchboard widget) conversation stamps the butler it
was first routed to, so follow-up messages can bypass re-classification via
``pinned_target=routed_butler``.

Column:
  routed_butler  TEXT NULL — the butler name the conversation's first message
                 was routed to by Switchboard classification. NULL for
                 pinned per-butler conversations (already deterministic) and
                 for widget conversations that haven't routed yet (e.g. a
                 bug-lane report, which never targets a domain butler).

Backward-compatible, additive-only: existing rows get NULL and keep working.

MIGRATION SERIALIZATION: this is the ONLY bead in epic bu-p6ey8 authorized to
add an Alembic migration — see the epic description for the collision
hazard this avoids.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_153"
down_revision = "core_152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            ADD COLUMN IF NOT EXISTS routed_butler TEXT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            DROP COLUMN IF EXISTS routed_butler
        """
    )
