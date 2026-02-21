"""make scheduled_tasks.prompt nullable for job dispatch mode

Revision ID: core_004
Revises: core_003
Create Date: 2026-02-22 00:00:00.000000

Legacy scheduled_tasks tables had prompt TEXT NOT NULL. The target state
(core_001) defines prompt as nullable because job-mode schedules have no
prompt. Drop the NOT NULL constraint so job-mode inserts/updates succeed.
"""

from __future__ import annotations

from alembic import op

revision = "core_004"
down_revision = "core_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE scheduled_tasks ALTER COLUMN prompt DROP NOT NULL")


def downgrade() -> None:
    op.execute(
        """
        UPDATE scheduled_tasks SET prompt = '' WHERE prompt IS NULL;
        ALTER TABLE scheduled_tasks ALTER COLUMN prompt SET NOT NULL;
        """
    )
