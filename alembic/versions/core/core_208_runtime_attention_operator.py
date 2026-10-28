"""Converge the current attention boundary on its historical revision edge.

The revision identity remains historical; its privileged singleton, installer
handoff and interface-version ladder do not. The one source-owned maintenance
installer verifies existing protected state or creates the final shape on the
same selected, lock-owning connection. No serving role receives that authority.
Protected operator rollback is implemented separately from a version restamp;
these edges never erase receipts, generations or retained delivery evidence.
"""

from alembic import op
from butlers.db_authority.interfaces import install_revision_group, require_boundary_absence

revision = "core_208"
down_revision = "core_207"
branch_labels = None
depends_on = None


def upgrade() -> None:
    install_revision_group(op.get_bind(), "attention")


def downgrade() -> None:
    # A pinned operator R0 teardown may establish absence after its own exact
    # empty-state proof. R1 keeps protected state and does not cross this edge.
    # A plain Alembic command cannot silently substitute destructive teardown.
    require_boundary_absence(op.get_bind(), "attention")
