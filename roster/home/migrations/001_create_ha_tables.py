"""create_ha_tables

Revision ID: home_assistant_001
Revises:
Create Date: 2026-02-28 00:00:00.000000

Creates Home Assistant module tables in the butler's schema:

  - ha_entity_snapshot  — periodic snapshots of HA entity states for
                          offline access and trend analysis
  - ha_command_log      — audit trail of all HA service calls issued
                          by the butler, with structured result capture
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_assistant_001"
down_revision = None
branch_labels = ("home",)
depends_on = None


def upgrade() -> None:
    # ha_entity_snapshot: keyed by entity_id; one row per entity, upserted
    # on each snapshot cycle. Records the last-known state, full attributes
    # JSONB, HA's last_updated timestamp, and when the butler captured it.
    op.execute("""
        CREATE TABLE IF NOT EXISTS ha_entity_snapshot (
            entity_id    TEXT        PRIMARY KEY,
            state        TEXT,
            attributes   JSONB,
            last_updated TIMESTAMPTZ,
            captured_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ha_command_log: append-only audit log of every HA service call.
    # domain + service identify the HA action; target and data are the
    # service call payload; result captures the HA response; context_id
    # maps back to the HA event context for cross-referencing.
    op.execute("""
        CREATE TABLE IF NOT EXISTS ha_command_log (
            id         BIGSERIAL   PRIMARY KEY,
            domain     TEXT        NOT NULL,
            service    TEXT        NOT NULL,
            target     JSONB,
            data       JSONB,
            result     JSONB,
            context_id TEXT,
            issued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Index on issued_at to support time-range queries over the command log.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ha_command_log_issued_at
            ON ha_command_log (issued_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ha_command_log_issued_at")
    op.execute("DROP TABLE IF EXISTS ha_command_log")
    op.execute("DROP TABLE IF EXISTS ha_entity_snapshot")
