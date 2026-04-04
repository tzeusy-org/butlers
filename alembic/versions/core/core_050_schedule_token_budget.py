"""schedule_token_budget: add max_token_budget to scheduled_tasks.

Revision ID: core_050
Revises: core_049
Create Date: 2026-04-04 00:00:00.000000

Adds an optional ``max_token_budget`` INTEGER column to the per-butler
``scheduled_tasks`` table.  When set, the spawner will record a budget
overrun warning on sessions that exceed the limit.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_050"
down_revision = "core_049"
branch_labels = None
depends_on = None

# Schemas that contain a scheduled_tasks table (mirrors core_001 list).
_BUTLER_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)


def upgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = '{schema}' AND table_name = 'scheduled_tasks'
                ) THEN
                    ALTER TABLE {schema}.scheduled_tasks
                        ADD COLUMN IF NOT EXISTS max_token_budget INTEGER;
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = '{schema}' AND table_name = 'scheduled_tasks'
                ) THEN
                    ALTER TABLE {schema}.scheduled_tasks
                        DROP COLUMN IF EXISTS max_token_budget;
                END IF;
            END
            $$;
            """
        )
