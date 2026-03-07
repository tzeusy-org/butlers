"""Create source_filters and connector_source_filters tables.

Revision ID: sw_026
Revises: sw_025
Create Date: 2026-03-07 00:00:00.000000

Migration notes:
- source_filters: named filter registry (blacklist/whitelist) with open-text
  source_key_type (no CHECK constraint — validated at API layer per connector
  channel so new connector types require no migration).
- connector_source_filters: connector-to-filter assignment table with enabled
  flag and priority ordering. FK to source_filters with ON DELETE CASCADE so
  deleting a filter cleans up all its assignments automatically.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_026"
down_revision = "sw_025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_filters (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            filter_mode TEXT NOT NULL CHECK (filter_mode IN ('blacklist', 'whitelist')),
            source_key_type TEXT NOT NULL,
            patterns TEXT[] NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE connector_source_filters (
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            filter_id UUID NOT NULL REFERENCES source_filters(id) ON DELETE CASCADE,
            enabled BOOLEAN NOT NULL DEFAULT true,
            priority INTEGER NOT NULL DEFAULT 0,
            attached_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (connector_type, endpoint_identity, filter_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_connector_source_filters_connector
        ON connector_source_filters (connector_type, endpoint_identity)
        WHERE enabled = true
        """
    )

    op.execute(
        """
        CREATE INDEX ix_connector_source_filters_filter_id
        ON connector_source_filters (filter_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_source_filters CASCADE")
    op.execute("DROP TABLE IF EXISTS source_filters CASCADE")
