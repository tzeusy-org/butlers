"""Apply each correction field independently across the append-only history.

Revision ID: chronicler_043
Revises: chronicler_042
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_043"
down_revision = "chronicler_042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL means this row did not correct the field. The note and timestamp
    # retain their existing meaning: they describe the latest whole row.
    op.execute("""
        CREATE OR REPLACE VIEW v_latest_overrides AS
        SELECT target_kind, target_id,
            (array_agg(corrected_start_at ORDER BY created_at DESC, id DESC)
                FILTER (WHERE corrected_start_at IS NOT NULL))[1] AS corrected_start_at,
            (array_agg(corrected_end_at ORDER BY created_at DESC, id DESC)
                FILTER (WHERE corrected_end_at IS NOT NULL))[1] AS corrected_end_at,
            (array_agg(corrected_title ORDER BY created_at DESC, id DESC)
                FILTER (WHERE corrected_title IS NOT NULL))[1] AS corrected_title,
            (array_agg(corrected_privacy ORDER BY created_at DESC, id DESC)
                FILTER (WHERE corrected_privacy IS NOT NULL))[1] AS corrected_privacy,
            (array_agg(corrected_tombstone_at ORDER BY created_at DESC, id DESC)
                FILTER (WHERE corrected_tombstone_at IS NOT NULL))[1] AS corrected_tombstone_at,
            (array_agg(note ORDER BY created_at DESC, id DESC))[1] AS note,
            max(created_at) AS corrected_at
        FROM overrides
        GROUP BY target_kind, target_id
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE VIEW v_latest_overrides AS
        SELECT DISTINCT ON (target_kind, target_id)
            target_kind, target_id, corrected_start_at, corrected_end_at,
            corrected_title, corrected_privacy, corrected_tombstone_at, note,
            created_at AS corrected_at
        FROM overrides
        ORDER BY target_kind, target_id, created_at DESC
    """)
