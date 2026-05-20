"""Data-fix: set connector_registry.replay_safe = FALSE for Gmail connectors.

Revision ID: sw_013
Revises: sw_012
Create Date: 2026-05-20 00:00:00.000000

Per the connector-replay-idempotency-policy spec, the Gmail connector is
NOT replay-safe: replaying a Gmail-sourced event can re-trigger outbound
actions (e.g. duplicate email replies).  Migration sw_012 added the
``replay_safe`` column with ``DEFAULT TRUE``; this follow-up data-fix
sets ``replay_safe = FALSE`` for all existing ``connector_type = 'gmail'``
rows as required by the spec.

The UPDATE is idempotent — re-running the upgrade against an already-fixed
database is a no-op (the WHERE clause matches only rows that still have
``replay_safe = TRUE``).

Downgrade
---------
Restores Gmail rows to ``replay_safe = TRUE``.  This is safe in dev/test
environments.  In production, manual operator review is required before
downgrading because restoring TRUE makes Gmail events eligible for replay,
which the spec explicitly prohibits.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_013"
down_revision = "sw_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: only touches rows that still have replay_safe = TRUE.
    op.execute(
        """
        UPDATE connector_registry
           SET replay_safe = FALSE
         WHERE connector_type IN ('gmail')
           AND replay_safe = TRUE
        """
    )


def downgrade() -> None:
    # Restore Gmail rows to the column default (TRUE).
    # NOTE: in production, verify no replay actions relied on this being FALSE
    # before running a downgrade.
    op.execute(
        """
        UPDATE connector_registry
           SET replay_safe = TRUE
         WHERE connector_type IN ('gmail')
           AND replay_safe = FALSE
        """
    )
