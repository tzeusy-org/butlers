"""Add evaluator_notes column to quiz_responses.

Revision ID: education_002
Revises: education_001
Create Date: 2026-06-14 00:00:00.000000

Adds a nullable TEXT ``evaluator_notes`` column to ``education.quiz_responses``.
The ``mastery_record_response`` INSERT (roster/education/tools/mastery.py) and the
quiz-history SELECT (roster/education/api/router.py) both reference this column,
but the original 001 migration never created it — causing a hard 500 on both the
quiz-history endpoint and the core teaching write path. This forward migration
closes that schema drift.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "education_002"
down_revision = "education_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE education.quiz_responses
        ADD COLUMN IF NOT EXISTS evaluator_notes TEXT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE education.quiz_responses
        DROP COLUMN IF EXISTS evaluator_notes
    """)
