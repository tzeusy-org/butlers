"""attention_ledger: add 'approvals' to the source CHECK constraint.

Revision ID: core_227
Revises: core_226
Create Date: 2026-09-09 00:00:02.000000

bu-2jtfw.11. A prepared action (``pending_actions.origin='prepared'``) is
parked silently -- it has no ``notify``/``insight`` egress attempt of its own
to record a failure against, since it is never pushed to the owner. When one
fails execution on approve, ``execute_approved_action`` records an
``attention_ledger`` row with ``source='approvals'`` (see
``butlers.core.attention_ledger``'s ``Source`` literal, widened in the same
PR). This migration widens the DB-level ``chk_attention_ledger_source``
CHECK to accept it, following the exact pattern core_171 used to add
``'discretion'``.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_227"
down_revision = "core_226"
branch_labels = None
depends_on = None

_OLD_SOURCES = ("notify", "insight", "discretion")
_NEW_SOURCES = (*_OLD_SOURCES, "approvals")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.attention_ledger
        DROP CONSTRAINT IF EXISTS chk_attention_ledger_source
    """)
    op.execute(f"""
        ALTER TABLE public.attention_ledger
        ADD CONSTRAINT chk_attention_ledger_source
        CHECK (source IN ({", ".join(f"'{s}'" for s in _NEW_SOURCES)}))
    """)


def downgrade() -> None:
    # Existing 'approvals' rows would violate the narrower constraint -- delete
    # them so the downgrade never leaves the table in a state the old
    # constraint rejects. These are audit-only observability rows (a failed
    # prepared-action execution); they carry no downstream FK or delivery
    # state, so dropping them on downgrade is safe.
    op.execute("""
        DELETE FROM public.attention_ledger
        WHERE source = 'approvals'
    """)
    op.execute("""
        ALTER TABLE public.attention_ledger
        DROP CONSTRAINT IF EXISTS chk_attention_ledger_source
    """)
    op.execute(f"""
        ALTER TABLE public.attention_ledger
        ADD CONSTRAINT chk_attention_ledger_source
        CHECK (source IN ({", ".join(f"'{s}'" for s in _OLD_SOURCES)}))
    """)
