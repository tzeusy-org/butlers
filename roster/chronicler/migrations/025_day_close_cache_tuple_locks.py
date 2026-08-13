"""add collision-safe exact tuple locks for day-close cache writers

Revision ID: chronicler_025
Revises: chronicler_024
Create Date: 2026-08-13 00:00:00.000000

The day-close writer must serialize invalid-candidate containment with a
concurrent valid write for the exact selected ``(local_date, timezone)`` tuple.
PostgreSQL advisory locks take fixed-width integer values, so hashing an exact
IANA timezone cannot guarantee that different tuples never contend. This small
Chronicler-local registry uses the actual tuple as its primary key. It contains
only lock identities; it does not alter, backfill, or delete any cache or
historical row.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_025"
down_revision = "chronicler_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS day_close_cache_locks (
            local_date DATE NOT NULL,
            timezone TEXT NOT NULL,
            PRIMARY KEY (local_date, timezone)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS day_close_cache_locks")
