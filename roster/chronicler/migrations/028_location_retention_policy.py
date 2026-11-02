"""add the owner-declared OwnTracks location retention policy record

Revision ID: chronicler_028
Revises: chronicler_027
Create Date: 2026-11-02 00:00:00.000000

Raw OwnTracks fixes are promised a declared retention horizon: 30 days by
default, which the owner may shorten (1..30 days) but never widen past the
default (bu-s11n0s.7). This migration adds the canonical typed policy record
that later expiry work freezes into its batches.

The table holds exactly one row (``id = 1``). ``days`` is bounded by a CHECK so
a malformed write cannot reach the row, and ``version`` is a compare-and-set
counter the protected dashboard setter increments. The default row is seeded
here, at trusted migration time, never from model or caller input.

No location data is read, altered or deleted by this revision.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_028"
down_revision = "chronicler_027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS location_retention_policy (
            id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            days INTEGER NOT NULL DEFAULT 30 CHECK (days BETWEEN 1 AND 30),
            version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by TEXT NOT NULL DEFAULT 'system:default'
        )
        """
    )
    op.execute("INSERT INTO location_retention_policy (id) VALUES (1) ON CONFLICT (id) DO NOTHING")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS location_retention_policy")
