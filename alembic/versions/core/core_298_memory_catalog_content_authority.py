"""Carry the source row's content authority on public.memory_catalog rows.

Revision ID: core_298
Revises: core_297
Create Date: 2026-11-02 00:00:00.000000

bu-q7vx1q.25, REQ-module-memory-013 "Catalog readers attribute non-owner
content". Since mem_013 the authority of a fact or rule lives only in the
owning butler's ``facts``/``rules`` rows, which cross-butler catalog readers
cannot join. This adds a nullable ``content_authority`` column with the same
vocabulary as mem_013 so ``search_catalog`` and its callers can tell an
owner-stated fact from one a third party sent.

Only the authority class is stored, never the sender entity: the public table
is readable by every butler role, and the sender stays in the owning schema.

No backfill guessing here: existing rows start NULL (rendered as unverified)
and each butler's ``run_memory_catalog_backfill`` copies the authority from its
own source rows, so a legacy NULL source stays NULL.

Additive-only: a nullable column needs no table rewrite.
"""

from __future__ import annotations

from alembic import op

revision = "core_298"
down_revision = "core_297"
branch_labels = None
depends_on = None

_AUTHORITIES = "('owner', 'owner_device', 'third_party', 'system', 'mixed')"


def upgrade() -> None:
    op.execute("ALTER TABLE public.memory_catalog ADD COLUMN IF NOT EXISTS content_authority TEXT")
    op.execute(
        "ALTER TABLE public.memory_catalog"
        " DROP CONSTRAINT IF EXISTS memory_catalog_content_authority_check"
    )
    op.execute(
        f"""
        ALTER TABLE public.memory_catalog
        ADD CONSTRAINT memory_catalog_content_authority_check
        CHECK (content_authority IS NULL OR content_authority IN {_AUTHORITIES})
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.memory_catalog"
        " DROP CONSTRAINT IF EXISTS memory_catalog_content_authority_check"
    )
    op.execute("ALTER TABLE public.memory_catalog DROP COLUMN IF EXISTS content_authority")
