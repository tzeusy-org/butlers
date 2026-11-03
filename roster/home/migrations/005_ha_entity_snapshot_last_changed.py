"""ha_entity_snapshot.last_changed — when the entity's state last changed (bu-jv1moa.18).

Revision ID: home_005
Revises: home_004
Create Date: 2026-10-19 00:00:00.000000

Home Assistant bumps ``last_updated`` on any attribute change but moves ``last_changed`` only
on a state change.  The safety sentinel's debounce needs the latter, so a sensor that has been
``on`` for an hour is not mistaken for one that just turned on.  The column is nullable: a row
written before this revision has no value until the Home module's next snapshot persist, and
readers fall back to ``last_updated`` for it.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_005"
down_revision = "home_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ha_entity_snapshot ADD COLUMN IF NOT EXISTS last_changed TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE ha_entity_snapshot DROP COLUMN IF EXISTS last_changed")
