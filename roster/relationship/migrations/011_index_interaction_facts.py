"""index_interaction_facts

Add a partial B-tree index on facts(entity_id, valid_at DESC) for
interaction_* predicates, mirroring the existing meal_*, transaction_*, and
measurement_* partial indexes in the memory migration chain.

The two new facts-based queries in GET /contacts and GET /contacts/{id} (added
in PR #1287) join facts on entity_id with predicate LIKE 'interaction_%',
validity = 'active', and scope = 'relationship'.  Without a dedicated index,
both queries degrade to a full-table scan on the facts relation.

Guard: if the memory module is not installed the facts table will not exist;
the migration skips index creation (and downgrade is a no-op) so the
relationship chain can be applied independently.

Revision ID: rel_011
Revises: rel_010
Create Date: 2026-04-30 00:00:00.000000
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_011"
down_revision = "rel_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    facts_exists = conn.execute(text("SELECT to_regclass('facts')")).scalar()
    if facts_exists is None:
        return

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_interaction_entity_valid_at
        ON facts (entity_id, valid_at DESC)
        WHERE predicate >= 'interaction_'
          AND predicate < 'interaction`'
          AND validity = 'active'
          AND scope = 'relationship'
    """)


def downgrade() -> None:
    conn = op.get_bind()
    facts_exists = conn.execute(text("SELECT to_regclass('facts')")).scalar()
    if facts_exists is None:
        return

    op.execute("DROP INDEX IF EXISTS idx_facts_interaction_entity_valid_at")
