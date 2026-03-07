"""facts_gin_indexes

Adds GIN index on facts.metadata for JSONB containment queries and partial
B-tree indexes on (predicate, valid_at) for the three high-volume temporal
predicate families used by aggregation tools (nutrition_summary,
spending_summary, trend_report).

Index design:
1. GIN index on facts.metadata — supports @>, ?, ?|, ?& operators.
2. Partial B-tree on (predicate, valid_at) WHERE predicate LIKE 'meal_%'
   AND validity = 'active' — meal/nutrition aggregation.
3. Partial B-tree on (predicate, valid_at) WHERE predicate LIKE 'transaction_%'
   AND validity = 'active' — spending aggregation.
4. Partial B-tree on (predicate, valid_at) WHERE predicate LIKE 'measurement_%'
   AND validity = 'active' — health trend queries.

Note: PostgreSQL partial indexes do not support LIKE predicates in the WHERE
clause directly. The standard workaround is to use text_pattern_ops or to
materialise the prefix check via a generated column. However, the most
practical and portable approach for prefix filtering with partial indexes is
to bound the range via >= / < on the predicate text. This migration uses
that approach so the planner can use the index for predicate scans:
  - 'meal_'    <= predicate < 'meal`'   (backtick is ASCII 96, one above 'Z' 95;
                                         underscore is 95, backtick is 96)
  - 'transaction_' / 'measurement_' handled the same way.

All indexes are created with IF NOT EXISTS (idempotent).

Revision ID: mem_009
Revises: mem_008
Create Date: 2026-03-08 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_009"
down_revision = "mem_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. GIN index on facts.metadata for general JSONB containment queries.
    #    Supports operators: @>, ?, ?|, ?& (e.g. metadata @> '{"key": "val"}')
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_metadata_gin
        ON facts USING gin(metadata)
    """)

    # 2. Partial B-tree index for meal aggregation (nutrition_summary).
    #    Covers predicates in the 'meal_*' family (meal_breakfast, meal_lunch,
    #    meal_dinner, meal_snack, ...) for active temporal facts.
    #    Range predicate simulates LIKE 'meal_%': predicate >= 'meal_' AND
    #    predicate < 'meal`'  (character after '_' in ASCII is '`' = 0x60).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_meal_predicate_valid_at
        ON facts (predicate, valid_at)
        WHERE predicate >= 'meal_'
          AND predicate < 'meal`'
          AND validity = 'active'
    """)

    # 3. Partial B-tree index for spending aggregation (spending_summary).
    #    Covers predicates in the 'transaction_*' family.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_transaction_predicate_valid_at
        ON facts (predicate, valid_at)
        WHERE predicate >= 'transaction_'
          AND predicate < 'transaction`'
          AND validity = 'active'
    """)

    # 4. Partial B-tree index for health trend queries (trend_report).
    #    Covers predicates in the 'measurement_*' family.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_measurement_predicate_valid_at
        ON facts (predicate, valid_at)
        WHERE predicate >= 'measurement_'
          AND predicate < 'measurement`'
          AND validity = 'active'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_facts_measurement_predicate_valid_at")
    op.execute("DROP INDEX IF EXISTS idx_facts_transaction_predicate_valid_at")
    op.execute("DROP INDEX IF EXISTS idx_facts_meal_predicate_valid_at")
    op.execute("DROP INDEX IF EXISTS idx_facts_metadata_gin")
