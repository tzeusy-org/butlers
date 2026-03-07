"""sessions_request_id_not_null: promote sessions.request_id to NOT NULL

Revision ID: core_021
Revises: core_020
Create Date: 2026-03-07 00:00:00.000000

Backfills any existing NULL request_id values with a fresh UUID, then
applies the NOT NULL constraint.  After this migration every session row
carries a non-null UUID7-compatible request_id, ensuring the invariant that
all sessions — connector-sourced or internal — are attributable to a single
request identifier.

This migration runs once per butler schema context; the unqualified sessions
table resolves to the schema-specific table via the active search_path.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_021"
down_revision = "core_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill any pre-existing NULL rows before tightening the constraint.
    op.execute("""
        UPDATE sessions
        SET request_id = gen_random_uuid()::TEXT
        WHERE request_id IS NULL
    """)
    op.execute("""
        ALTER TABLE sessions
        ALTER COLUMN request_id SET NOT NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE sessions
        ALTER COLUMN request_id DROP NOT NULL
    """)
