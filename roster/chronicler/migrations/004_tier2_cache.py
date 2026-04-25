"""add_tier2_cache_table

Revision ID: chronicler_004
Revises: chronicler_003
Create Date: 2026-04-25 00:00:00.000000

Creates ``tier2_cache`` to persist day-close prose summaries produced by
Tier 2 (LLM) interpretation.

Each row caches the prose output for a specific day-close window, keyed by
``day_close:{YYYY-MM-DD}``.  A ``superseded_at`` timestamp marks stale
entries without deletion so provenance history is retained.

Indexes on ``(start_at, end_at)`` support the window-overlap staleness
check: when a projection rebuild covers the same span, it can find and
supersede the existing cache row before inserting the new one.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "chronicler_004"
down_revision = "chronicler_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tier2_cache ──────────────────────────────────────────────────────
    # Stores day-close LLM-generated prose summaries.
    op.execute("""
        CREATE TABLE IF NOT EXISTS tier2_cache (
            cache_key          TEXT PRIMARY KEY,
            start_at           TIMESTAMPTZ NOT NULL,
            end_at             TIMESTAMPTZ NOT NULL,
            cache_built_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            prose              TEXT NOT NULL,
            provenance_refs    JSONB NOT NULL DEFAULT '[]'::jsonb,
            superseded_at      TIMESTAMPTZ,
            CHECK (end_at >= start_at)
        )
    """)

    # Index for window-overlap staleness check: lookup by start_at range.
    op.execute("""
        CREATE INDEX IF NOT EXISTS tier2_cache_start_at_idx
        ON tier2_cache (start_at)
        WHERE superseded_at IS NULL
    """)

    # Index for window-overlap staleness check: lookup by end_at range.
    op.execute("""
        CREATE INDEX IF NOT EXISTS tier2_cache_end_at_idx
        ON tier2_cache (end_at)
        WHERE superseded_at IS NULL
    """)

    # Composite index for the most common staleness query:
    # WHERE start_at <= $x AND end_at >= $y AND superseded_at IS NULL
    op.execute("""
        CREATE INDEX IF NOT EXISTS tier2_cache_window_idx
        ON tier2_cache (start_at, end_at)
        WHERE superseded_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tier2_cache")
