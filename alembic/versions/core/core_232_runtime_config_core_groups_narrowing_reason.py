"""Add explicit core-groups narrowing authority to runtime config.

Revision ID: core_232
Revises: core_231
Create Date: 2026-09-13 00:00:00.000000

bu-h40h2b.1: Git-declared core groups are authoritative. A runtime row may
retain a strict subset only when this field records the operator's reason.
Existing rows default to NULL so stale first-boot snapshots are reconciled on
the next daemon start instead of silently suppressing new Git capabilities.
"""

from __future__ import annotations

from alembic import op

revision = "core_232"
down_revision = "core_231"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runtime_config
        ADD COLUMN IF NOT EXISTS core_groups_narrowing_reason text
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_core_groups_reconciled_toml_digest
        ON public.audit_log (target, (metadata ->> 'toml_digest'))
        WHERE action = 'core_groups_reconciled'
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_config
        DROP CONSTRAINT IF EXISTS ck_runtime_config_core_groups_narrowing_reason
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_config
        ADD CONSTRAINT ck_runtime_config_core_groups_narrowing_reason
        CHECK (
            core_groups_narrowing_reason IS NULL
            OR btrim(core_groups_narrowing_reason) <> ''
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS public.uq_audit_core_groups_reconciled_toml_digest
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_config
        DROP CONSTRAINT IF EXISTS ck_runtime_config_core_groups_narrowing_reason
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_config
        DROP COLUMN IF EXISTS core_groups_narrowing_reason
        """
    )
