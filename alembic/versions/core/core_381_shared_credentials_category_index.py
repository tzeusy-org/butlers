"""Provision the shared credential category index for schema-scoped core chains.

Revision ID: core_381
Revises: core_374
"""

from __future__ import annotations

from alembic import op

revision = "core_381"
down_revision = "core_374"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # core_117 creates this public table even when core_001 runs in a butler
    # schema. The index belongs to the table's schema, not the search path.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_butler_secrets_category ON public.butler_secrets (category)"
    )


def downgrade() -> None:
    # A public-first core_001 or another schema may already own this index.
    # Keep this harmless repair so rolling back one consumer cannot remove it.
    pass
