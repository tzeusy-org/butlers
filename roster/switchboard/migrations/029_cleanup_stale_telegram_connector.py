"""Clean up stale telegram:BigButlerBot connector registry entry.

Revision ID: sw_029
Revises: sw_028
Create Date: 2026-03-11 00:00:00.000000

Migration notes:
- Removes the stale connector_registry entry with connector_type='telegram'
  and endpoint_identity='BigButlerBot'. This phantom entry was created by an
  older connector version before the type was corrected to 'telegram_bot'.
- The real connector uses connector_type='telegram_bot' and should be preserved.
- This is a one-way cleanup; downgrade does not restore the entry.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_029"
down_revision = "sw_028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Delete the stale phantom entry with connector_type='telegram'
    # Keep any existing 'telegram_bot' entries for the same endpoint
    op.execute(
        """
        DELETE FROM connector_registry
        WHERE connector_type = 'telegram' AND endpoint_identity = 'BigButlerBot'
        """
    )


def downgrade() -> None:
    # No downgrade: the stale entry is not restored
    pass
