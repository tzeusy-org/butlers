"""ha_source_health — tracks whether the Home Assistant connection is actually healthy.

Revision ID: home_003
Revises: home_002
Create Date: 2026-09-05 00:00:00.000000

Single-row-per-source health ledger for the Home Assistant connection.
``HomeAssistantModule`` upserts this row on every successful WS auth / REST
poll and on every connect/poll failure, so snapshot readers can distinguish
"HA is actually healthy right now" from "the cached snapshot merely looks
fresh because captured_at is always stamped now()" — the trust defect
tracked by bu-8cdl1.12 slice 1 (failure must never impersonate health).
"""

from __future__ import annotations

from alembic import op

revision = "home_003"
down_revision = "home_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ha_source_health (
            source           TEXT        PRIMARY KEY,
            status           TEXT        NOT NULL
                             CHECK (status IN ('healthy', 'error')),
            last_success_at  TIMESTAMPTZ,
            last_error_at    TIMESTAMPTZ,
            last_error       TEXT,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ha_source_health")
