"""quiz_evaluator_notes

Add evaluator_notes column to quiz_responses for storing LLM reasoning
about quality scores.

Revision ID: education_003
Revises: education_002
Create Date: 2026-02-28 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "education_003"
down_revision = "education_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE education.quiz_responses
            ADD COLUMN IF NOT EXISTS evaluator_notes TEXT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE education.quiz_responses DROP COLUMN IF EXISTS evaluator_notes")
