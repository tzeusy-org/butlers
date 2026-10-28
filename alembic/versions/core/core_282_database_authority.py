"""Source-owned final database authority and preserving operator reverse.

Revision ID: core_282
Revises: core_281

The delivered core281/core280 lineage is retained. Application/evidence rows and all
protected generation/receipt/outbox state survive both directions. Full object
ACL convergence follows all selected chains under the same migration session.
The reverse edge requires the explicit pinned R1 operator procedure; it does
not implement a serving fallback or a stamp-only downgrade.
"""

from alembic import op
from butlers.db_authority.apply import install_final_revision, require_preserving_reverse

revision = "core_282"
down_revision = "core_281"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install_final_revision(op.get_bind())


def downgrade() -> None:
    require_preserving_reverse(op.get_bind())
