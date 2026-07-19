"""Repair missing failure outcomes in historical audit evidence.

Revision ID: core_178
Revises: core_177
Create Date: 2026-07-19 00:00:00.000000

Credential probe writers historically recorded ``action = 'failed'`` while
leaving ``result`` NULL. `/api/issues` groups only ``result = 'error'`` rows,
so that known failure evidence could never enter the issue spine. This repair
is intentionally narrow and idempotent: it fills only the missing outcome for
that exact action, preserving every other field and every already-classified
row. The append-only audit contract otherwise remains unchanged.
"""

from __future__ import annotations

from alembic import op

revision = "core_178"
down_revision = "core_177"
branch_labels = None
depends_on = None


REPAIR_FAILED_OUTCOMES_SQL = """
DO $$
BEGIN
    IF to_regclass('public.audit_log') IS NULL THEN
        RETURN;
    END IF;

    UPDATE public.audit_log
    SET result = 'error'
    WHERE action = 'failed'
      AND result IS NULL;
END
$$;
"""


def upgrade() -> None:
    """Fill the one proven missing outcome without rewriting other evidence."""
    # Core migrations run against multiple schema contexts, while audit_log is
    # public; the WHERE guard makes every subsequent execution a no-op.
    op.execute(REPAIR_FAILED_OUTCOMES_SQL)


def downgrade() -> None:
    """Preserve repaired append-only evidence rather than attempting a rollback."""
