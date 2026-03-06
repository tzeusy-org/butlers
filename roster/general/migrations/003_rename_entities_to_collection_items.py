"""rename_entities_to_collection_items

Revision ID: gen_003
Revises: gen_002
Create Date: 2026-03-06 00:00:00.000000

Resolves naming collision with shared.entities (identity entities table).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "gen_003"
down_revision = "gen_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE entities RENAME TO collection_items")
    op.execute("ALTER INDEX idx_entities_data_gin RENAME TO idx_collection_items_data_gin")
    op.execute(
        "ALTER INDEX idx_entities_collection_id RENAME TO idx_collection_items_collection_id"
    )
    op.execute("ALTER INDEX idx_entities_tags_gin RENAME TO idx_collection_items_tags_gin")


def downgrade() -> None:
    op.execute("ALTER TABLE collection_items RENAME TO entities")
    op.execute("ALTER INDEX idx_collection_items_data_gin RENAME TO idx_entities_data_gin")
    op.execute(
        "ALTER INDEX idx_collection_items_collection_id RENAME TO idx_entities_collection_id"
    )
    op.execute("ALTER INDEX idx_collection_items_tags_gin RENAME TO idx_entities_tags_gin")
