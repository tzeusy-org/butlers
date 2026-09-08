"""Add retired_at to rules — active/retired lifecycle status (bu-6t8ix.3).

Revision ID: mem_012
Revises: mem_011

The Rule schema previously had no active/retired concept: the only
soft-delete signal was the JSONB ``metadata->>'forgotten'`` flag, which means
"this rule was wrong" (retracted, like a fact). Retiring a rule is a distinct
action: the rule may still be correct, but the owner has decided it no longer
needs to be enforced. ``retired_at`` is a nullable timestamp rather than a
boolean so "when was this retired" stays answerable, and rather than a status
enum because there is exactly one non-active state today; a timestamp already
carries the audit information an enum would need a companion column for.

Additive-only: adding a nullable column is non-blocking on PostgreSQL (no
table rewrite, no default to backfill).
"""

from __future__ import annotations

from alembic import op

revision = "mem_012"
down_revision = "mem_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE rules
        ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE rules
        DROP COLUMN IF EXISTS retired_at
    """)
