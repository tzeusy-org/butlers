"""healing_attempts: add dispatch_pending status to partial unique index

Revision ID: core_038
Revises: core_037
Create Date: 2026-03-17 00:00:00.000000

The retry endpoint (POST /api/healing/attempts/{id}/retry) can now create rows
with status ``dispatch_pending`` when no in-process dispatch hook is wired.
This distinguishes deferred-dispatch retry rows from crash-orphaned
``investigating`` rows, letting ``recover_stale_attempts`` handle them
differently on daemon restart.

Changes:
  1. Drops the existing partial unique index
     ``uq_healing_attempts_active_fingerprint`` (which only covers
     ``investigating`` and ``pr_open``) and recreates it to also include
     ``dispatch_pending``.  This prevents duplicate active rows per fingerprint
     regardless of whether they are in the pre-dispatch or investigating phase.

The schema of shared.healing_attempts itself is unchanged — ``dispatch_pending``
is a valid text value for the existing TEXT status column.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_038"
down_revision = "core_037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old partial unique index (investigating, pr_open only) and
    # recreate it to also cover dispatch_pending.
    op.execute(
        "DROP INDEX IF EXISTS shared.uq_healing_attempts_active_fingerprint"
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_healing_attempts_active_fingerprint
        ON shared.healing_attempts (fingerprint)
        WHERE status IN ('dispatch_pending', 'investigating', 'pr_open')
    """)


def downgrade() -> None:
    # Revert to the original index that only covers investigating and pr_open.
    # Note: any existing dispatch_pending rows will no longer be covered by the
    # uniqueness constraint after downgrade, but the table remains intact.
    op.execute(
        "DROP INDEX IF EXISTS shared.uq_healing_attempts_active_fingerprint"
    )
    op.execute("""
        CREATE UNIQUE INDEX uq_healing_attempts_active_fingerprint
        ON shared.healing_attempts (fingerprint)
        WHERE status IN ('investigating', 'pr_open')
    """)
