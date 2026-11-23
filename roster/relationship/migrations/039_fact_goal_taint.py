"""Goal taint on entity facts (RFC 0038 D6, bu-pkuny2).

Revision ID: rel_039
Revises: rel_038

A fact asserted under an exposed broker assertion stamps ``taint_goal_id`` and
``taint_invocation_id``, the lineage pointer of the goal whose exposure wrote
it, next to the rel_037 content-authority stamp. NULL is untainted or
pre-switch.

Additive-only: nullable columns need no table rewrite. The rel_037 report
guard trigger does not cover these columns; nothing updates them.
"""

from __future__ import annotations

from alembic import op

revision = "rel_039"
down_revision = "rel_038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE relationship.entity_facts "
        "ADD COLUMN IF NOT EXISTS taint_goal_id UUID, "
        "ADD COLUMN IF NOT EXISTS taint_invocation_id UUID"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE relationship.entity_facts "
        "DROP COLUMN IF EXISTS taint_invocation_id, "
        "DROP COLUMN IF EXISTS taint_goal_id"
    )
