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

import sqlalchemy as sa

from alembic import op

revision = "core_232"
down_revision = "core_231"
branch_labels = None
depends_on = None

_PROTECTED_ROLLBACK_ERROR = (
    "core_232 cannot enter non-transactional downgrade work because the protected "
    "core_198 rollback preflight failed"
)


def _downgrade_crosses_core_198() -> bool:
    """Return whether this Alembic invocation will actually downgrade core_198."""
    from butlers.migrations import _chain_script_directory

    destination = op.get_context().opts.get("destination_rev")
    if not isinstance(destination, str):
        return True
    script = _chain_script_directory("core")
    steps = script._downgrade_revs(destination, (revision,))
    return any(step.revision.revision == "core_198" for step in steps)


def _protected_rollback_preflight_passes(bind: sa.Connection) -> bool:
    """Delegate to core_198's complete, canonical role/catalog/ACL proof."""
    from butlers.migrations import _chain_script_directory

    protected_revision = _chain_script_directory("core").get_revision("core_198")
    return protected_revision.module.protected_rollback_preflight_passes(bind)


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
    # core_231's downgrade enters an autocommit block. Only a downgrade that
    # will continue through core_198 needs this preflight; a bounded core_232
    # -> core_231 rollback must remain independently reversible. If the deep
    # rollback is unsafe, fail before core_232's DDL or stamp can be committed.
    if _downgrade_crosses_core_198() and not _protected_rollback_preflight_passes(op.get_bind()):
        raise RuntimeError(_PROTECTED_ROLLBACK_ERROR)
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
