"""Persist detected Steam changes for timed session evidence (bu-r3fmja.7).

Revision ID: core_314
Revises: core_313

Additive, shared across schema-local core installations. Downgrade intentionally
retains append-only evidence and its reader grants: source rollback disables the
producer/consumer; deleting observed history would be irreversible data loss.
"""

from __future__ import annotations

from alembic import op

revision = "core_314"
down_revision = "core_313"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('butlers:steam-status-history', 0))")
    op.execute("""
        CREATE TABLE IF NOT EXISTS connectors.steam_status_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            steam_id BIGINT NOT NULL,
            steam_account_id UUID,
            observed_at TIMESTAMPTZ NOT NULL,
            persona_state INTEGER NOT NULL,
            game_id TEXT,
            game_name TEXT,
            prev_game_name TEXT,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_steam_status_history_observation UNIQUE (steam_id, observed_at)
        )
    """)
    op.execute("GRANT USAGE ON SCHEMA connectors TO butler_chronicler_rw, connector_writer")
    op.execute("REVOKE ALL ON connectors.steam_status_history FROM PUBLIC, butler_chronicler_rw")
    op.execute("GRANT SELECT ON connectors.steam_status_history TO butler_chronicler_rw")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON connectors.steam_status_history TO connector_writer"
    )


def downgrade() -> None:
    # Keeping observations also makes rollback safe with other schema-local core users.
    pass
