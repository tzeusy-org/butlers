"""Entity rebind receipts may settle as ``skipped_forgotten`` (bu-dhloi1.71).

Revision ID: core_326
Revises: core_325
Create Date: 2026-11-09 00:00:00.000000

A receipt whose target chain ends at a forgotten entity has no survivor to hold the
references. The owner's forget-stranded-refs decision settles it ``skipped_forgotten``
after the schema retracts the source's live facts through its own forget path, instead of
leaving it ``failed`` and retrying every fifteen minutes. The completion rule is the
terminal shape of ``active``: completed, no error class.

Both constraints are replaced under the advisory lock core_243 used, so concurrent butler
chains do not race in PostgreSQL's catalogs. Downgrade first moves any such receipt to
``failed`` with its original error class, then restores the core_243 constraints.
"""

from __future__ import annotations

from alembic import op

revision = "core_326"
down_revision = "core_325"
branch_labels = None
depends_on = None

_STATUS = "ck_entity_rebind_log_status"
_COMPLETION = "ck_entity_rebind_log_completion"


def _install(statuses: str, completion: str) -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtext('core_243_entity_rebind_log'))")
    op.execute(f"ALTER TABLE public.entity_rebind_log DROP CONSTRAINT IF EXISTS {_STATUS}")
    op.execute(f"ALTER TABLE public.entity_rebind_log DROP CONSTRAINT IF EXISTS {_COMPLETION}")
    op.execute(
        f"ALTER TABLE public.entity_rebind_log ADD CONSTRAINT {_STATUS}"
        f" CHECK (status IN ({statuses}))"
    )
    op.execute(
        f"ALTER TABLE public.entity_rebind_log ADD CONSTRAINT {_COMPLETION} CHECK ({completion})"
    )


_BASE = """
    (status = 'pending' AND completed_at IS NULL AND error_class IS NULL)
    OR (status = 'active' AND completed_at IS NOT NULL AND error_class IS NULL)
    OR (status = 'skipped_no_table' AND completed_at IS NOT NULL
        AND error_class = 'UndefinedTableError')
    OR (status = 'failed' AND completed_at IS NOT NULL AND error_class IS NOT NULL)
"""


def upgrade() -> None:
    _install(
        "'active', 'failed', 'pending', 'skipped_no_table', 'skipped_forgotten'",
        _BASE + "OR (status = 'skipped_forgotten' AND completed_at IS NOT NULL"
        " AND error_class IS NULL)",
    )


def downgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtext('core_243_entity_rebind_log'))")
    op.execute(f"ALTER TABLE public.entity_rebind_log DROP CONSTRAINT IF EXISTS {_COMPLETION}")
    op.execute(
        "UPDATE public.entity_rebind_log SET status = 'failed',"
        " error_class = 'EntityRebindTargetForgotten' WHERE status = 'skipped_forgotten'"
    )
    _install("'active', 'failed', 'pending', 'skipped_no_table'", _BASE)
