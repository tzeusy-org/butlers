"""Install bounded endpoint custody under the existing trusted bootstrap owner.

Revision ID: core_260
Revises: core_258

The numeric gap is intentional. The independently owned capture revision259
is not a prerequisite, source import, restored-history admission or runtime
proof. Core replay calls the fixed bootstrap installer and then validates its
actual installed identity. Runtime roles never own this boundary.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op
from butlers.core.custody_installed import verify_installed_functions

revision = "core_260"
down_revision = "core_258"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "SELECT pg_catalog.pg_advisory_xact_lock("
        "pg_catalog.hashtextextended('butlers:core_260:endpoint_custody', 0))"
    )
    op.execute("SELECT custody_admission.install_interface()")
    proof = op.get_bind().execute(text("SELECT custody_admission.prove_interface()")).scalar_one()
    verify_installed_functions(proof)


def downgrade() -> None:
    # Durable sources, decisions and dispositions are evidence. The bootstrap
    # function refuses a populated or currently enrolled boundary; it never
    # reopens held owner paths by silently deleting a table.
    op.execute("SELECT custody_admission.rollback_interface()")
