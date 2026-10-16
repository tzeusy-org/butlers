"""Reserve explicit dark goal-admission limits without enabling admission.

Revision ID: core_266
Revises: core_265
"""

from alembic import op

revision = "core_266"
down_revision = "core_265"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE runtime_config ADD COLUMN IF NOT EXISTS goal_admission_config JSONB")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM runtime_config WHERE goal_admission_config IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot discard configured goal admission limits';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE runtime_config DROP COLUMN IF EXISTS goal_admission_config")
