"""Add versioned approval-pattern fingerprints.

Revision ID: approvals_003
Revises: approvals_002
Create Date: 2026-07-17 00:00:00.000000

Version 1 fingerprints hashed every tool argument.  Version 2 fingerprints
only module-declared safety-critical arguments (or all arguments when no such
argument is declared).  Legacy evidence remains explicitly version 1 so it
cannot inflate a version-2 promotion threshold.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_003"
down_revision = "approvals_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add and backfill the version column on every persisted fingerprint."""
    for table_name in ("autonomy_approval_history", "autonomy_suggestions"):
        op.execute(
            f"ALTER TABLE IF EXISTS {table_name} "
            "ADD COLUMN IF NOT EXISTS fingerprint_version SMALLINT"
        )
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('{table_name}') IS NOT NULL THEN
                    UPDATE {table_name}
                    SET fingerprint_version = 1
                    WHERE fingerprint_version IS NULL;

                    ALTER TABLE {table_name}
                    ALTER COLUMN fingerprint_version SET DEFAULT 1;
                    ALTER TABLE {table_name}
                    ALTER COLUMN fingerprint_version SET NOT NULL;
                    ALTER TABLE {table_name}
                    DROP CONSTRAINT IF EXISTS {table_name}_fingerprint_version_check;
                    ALTER TABLE {table_name}
                    ADD CONSTRAINT {table_name}_fingerprint_version_check
                    CHECK (fingerprint_version IN (1, 2));
                END IF;
            END $$;
            """
        )

    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('autonomy_approval_history') IS NOT NULL THEN
                CREATE INDEX IF NOT EXISTS idx_autonomy_history_fingerprint_version
                ON autonomy_approval_history (pattern_fingerprint, fingerprint_version);
            END IF;
            IF to_regclass('autonomy_suggestions') IS NOT NULL THEN
                CREATE INDEX IF NOT EXISTS idx_autonomy_suggestions_fingerprint_version
                ON autonomy_suggestions (pattern_fingerprint, fingerprint_version);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Remove the version columns and their supporting indexes."""
    op.execute("DROP INDEX IF EXISTS idx_autonomy_history_fingerprint_version")
    op.execute("DROP INDEX IF EXISTS idx_autonomy_suggestions_fingerprint_version")
    for table_name in ("autonomy_approval_history", "autonomy_suggestions"):
        op.execute(
            f"ALTER TABLE IF EXISTS {table_name} "
            f"DROP CONSTRAINT IF EXISTS {table_name}_fingerprint_version_check"
        )
        op.execute(f"ALTER TABLE IF EXISTS {table_name} DROP COLUMN IF EXISTS fingerprint_version")
