"""connector_registry: add deleted_at (soft-delete) and replay_safe columns.

Revision ID: sw_012
Revises: sw_011
Create Date: 2026-05-18 00:00:00.000000

Phase 3c (bu-1f91v.8).  Extends ``connector_registry`` with two columns
supporting connector lifecycle management:

deleted_at
----------
``TIMESTAMPTZ NULL`` — soft-delete timestamp.  When set, the row is considered
deleted.  The ``disconnect`` action (§3.4 / 4.4) sets this field rather than
issuing a hard DELETE.  Default-active queries MUST filter ``deleted_at IS NULL``.
NULL means not deleted; a non-NULL value is the deletion timestamp.

replay_safe
-----------
``BOOLEAN NOT NULL DEFAULT TRUE`` — whether events ingested by this connector
are safe to replay.  Some connectors (e.g. email) produce non-idempotent
side effects on replay (sending duplicate emails).  For such connectors this
flag must be set to FALSE.

Per spec connector-replay-idempotency-policy the email connector seed should
mark replay_safe = FALSE.  This migration sets the default to TRUE (safe);
a follow-up data-fix or separate seed step (per spec) will set email connectors
to FALSE.

Downgrade
---------
Removes both columns cleanly.  Any rows with non-NULL deleted_at will lose
that information on downgrade — this is acceptable because downgrade is only
used in dev/test environments where hard deletes are permissible.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_012"
down_revision = "sw_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Soft-delete column: NULL = active, non-NULL = deleted (timestamp of deletion)
    op.execute(
        """
        ALTER TABLE connector_registry
            ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL
        """
    )

    # Replay-safety flag: TRUE = safe to replay; FALSE = replay may cause side effects
    op.execute(
        """
        ALTER TABLE connector_registry
            ADD COLUMN IF NOT EXISTS replay_safe BOOLEAN NOT NULL DEFAULT TRUE
        """
    )

    # Partial index to efficiently query active (non-deleted) connectors
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_connector_registry_active
        ON connector_registry (connector_type, endpoint_identity)
        WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_connector_registry_active")
    op.execute("ALTER TABLE connector_registry DROP COLUMN IF EXISTS replay_safe")
    op.execute("ALTER TABLE connector_registry DROP COLUMN IF EXISTS deleted_at")
