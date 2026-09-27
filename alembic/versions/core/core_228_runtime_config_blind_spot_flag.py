"""Add the blind_spot_preamble_enabled kill switch to runtime_config.

Revision ID: core_228
Revises: core_227
Create Date: 2026-09-09 00:00:00.000000

bu-2jtfw.13: the blind-spot preamble (declared expected-signal absence,
surfaced at spawn time) is additive and gated per-butler by this column so a
regression can be rolled back per-schema without a code deploy. Defaults to
TRUE — the preamble is byte-identical no-op text (i.e. absent) whenever every
declared signal is PRESENT, so enabling it does not change any existing
spawn's prompt until a real blind spot exists.
"""

from __future__ import annotations

from alembic import op

revision = "core_228"
down_revision = "core_227"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runtime_config
        ADD COLUMN IF NOT EXISTS blind_spot_preamble_enabled boolean NOT NULL DEFAULT true
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE runtime_config DROP COLUMN IF EXISTS blind_spot_preamble_enabled")
