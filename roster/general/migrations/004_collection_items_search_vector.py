"""collection_items: content_text + search_vector for capture_search

Revision ID: gen_004
Revises: gen_003
Create Date: 2026-09-09 00:00:00.000000

Adds a generated ``content_text`` projection of ``data`` (the item's JSONB
payload) and a generated ``search_vector`` tsvector over it, plus a GIN
index, so ``capture_search`` (bu-2jtfw.9) can do real keyword search instead
of the unbounded JSONB-containment scan ``item_search`` was limited to.
Mirrors the generated-column convention from
``alembic/versions/core/core_221_dashboard_messages_search_index.py``.

A pre-migration butler (this column/index absent) is a legitimate,
detectable state -- ``capture_search`` catches the resulting
``UndefinedColumnError`` and degrades to the containment path, flagging the
degradation rather than silently returning nothing.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "gen_004"
down_revision = "gen_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE collection_items
        ADD COLUMN IF NOT EXISTS content_text text
        GENERATED ALWAYS AS (data::text) STORED
    """)
    op.execute("""
        ALTER TABLE collection_items
        ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(data::text, ''))) STORED
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_collection_items_search_vector
        ON collection_items USING GIN (search_vector)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_collection_items_search_vector")
    op.execute("ALTER TABLE collection_items DROP COLUMN IF EXISTS search_vector")
    op.execute("ALTER TABLE collection_items DROP COLUMN IF EXISTS content_text")
