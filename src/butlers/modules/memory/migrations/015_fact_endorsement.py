"""Add owner endorsement (vouch) columns to facts (bu-jv1moa.14).

Revision ID: mem_015
Revises: mem_014

A fact written from a non-owner's words (``content_authority`` ``third_party`` or
``mixed``) is held: stored as an attributed report, never wearing the owner's
authority.  ``endorsed_at`` / ``endorsed_by`` record the owner's explicit vouch, the same
pair rules carry since mem_013.

Vouching is additive.  The fact keeps its original ``content_authority`` and
``authority_entity_id``; only these two columns change, so clearing ``endorsed_at``
restores the prior state exactly.

Additive-only: nullable columns need no table rewrite.
"""

from __future__ import annotations

from alembic import op

revision = "mem_015"
down_revision = "mem_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS endorsed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS endorsed_by UUID")


def downgrade() -> None:
    op.execute("ALTER TABLE facts DROP COLUMN IF EXISTS endorsed_by")
    op.execute("ALTER TABLE facts DROP COLUMN IF EXISTS endorsed_at")
