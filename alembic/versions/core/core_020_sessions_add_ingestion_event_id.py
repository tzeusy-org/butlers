"""sessions_add_ingestion_event_id: add ingestion_event_id FK to sessions

Revision ID: core_020
Revises: core_019
Create Date: 2026-03-07 00:00:00.000000

Adds a nullable ingestion_event_id UUID column to each butler's sessions
table, referencing shared.ingestion_events(id).  Connector-sourced sessions
set this to the same UUID7 as request_id; internally-triggered sessions
(tick, schedule, trigger) leave it NULL.

This migration runs once per butler schema context; the unqualified sessions
table resolves to the schema-specific table via the active search_path.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_020"
down_revision = "core_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE sessions
        ADD COLUMN IF NOT EXISTS ingestion_event_id UUID
            REFERENCES shared.ingestion_events(id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sessions_ingestion_event_id
        ON sessions (ingestion_event_id)
        WHERE ingestion_event_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sessions_ingestion_event_id")
    op.execute("""
        ALTER TABLE sessions
        DROP COLUMN IF EXISTS ingestion_event_id
    """)
