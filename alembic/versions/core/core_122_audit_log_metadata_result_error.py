"""audit_log: add metadata/result/error columns for writer unification.

Revision ID: core_122
Revises: core_121
Create Date: 2026-06-14 00:00:00.000000

Extends ``public.audit_log`` (created in core_092) with three nullable
columns so a later PR can route the richer Switchboard
``dashboard_audit_log`` writers into the canonical audit primitive:

Columns added
-------------
metadata  JSONB   structured request/operation context (path, trigger_source, …)
result    TEXT    outcome label (e.g. ``"success"`` / ``"error"``)
error     TEXT    error message when result is an error

All three are nullable with no default value so existing INSERTs that omit
them are completely unaffected (backward compatible). This is a pure
additive, low-regression schema change — no writer or reader behaviour
changes ship with it.

``ADD COLUMN IF NOT EXISTS`` makes the upgrade idempotent and safe on a DB
that already carries these columns.  The downgrade drops them.
"""

from __future__ import annotations

from alembic import op

revision = "core_122"
down_revision = "core_121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.audit_log
            ADD COLUMN IF NOT EXISTS metadata JSONB,
            ADD COLUMN IF NOT EXISTS result   TEXT,
            ADD COLUMN IF NOT EXISTS error    TEXT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.audit_log
            DROP COLUMN IF EXISTS error,
            DROP COLUMN IF EXISTS result,
            DROP COLUMN IF EXISTS metadata
        """
    )
