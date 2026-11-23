"""Goal taint on episodes, facts and rules (RFC 0038 D6, bu-pkuny2).

Revision ID: mem_017
Revises: mem_016

A write made under an exposed broker assertion stamps ``taint_goal_id`` and
``taint_invocation_id``: a lineage pointer, not a boolean, so containment can
find every row one exposed goal wrote. Contact notes and decision memory are
facts, so they carry the same columns. NULL is untainted or pre-switch.

Additive-only: nullable columns need no table rewrite.
"""

from __future__ import annotations

from alembic import op

revision = "mem_017"
down_revision = "mem_016"
branch_labels = None
depends_on = None

_ADD = (
    "ADD COLUMN IF NOT EXISTS taint_goal_id UUID, ADD COLUMN IF NOT EXISTS taint_invocation_id UUID"
)
_DROP = "DROP COLUMN IF EXISTS taint_invocation_id, DROP COLUMN IF EXISTS taint_goal_id"


def upgrade() -> None:
    op.execute("ALTER TABLE episodes " + _ADD)
    op.execute("ALTER TABLE facts " + _ADD)
    op.execute("ALTER TABLE rules " + _ADD)


def downgrade() -> None:
    op.execute("ALTER TABLE episodes " + _DROP)
    op.execute("ALTER TABLE facts " + _DROP)
    op.execute("ALTER TABLE rules " + _DROP)
