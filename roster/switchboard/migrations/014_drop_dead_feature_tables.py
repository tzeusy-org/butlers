"""Drop verified-dead switchboard feature tables: connector_source_filters, source_filters,
email_metadata_refs, fanout_execution_log.

Revision ID: sw_014
Revises: sw_013
Create Date: 2026-06-12 00:00:00.000000

All four tables have 0 runtime code references and 0 rows in the dev database.
CREATE locations:
  - connector_source_filters, source_filters: 003_switchboard_routing.py (sw_003)
  - email_metadata_refs: 004_switchboard_email.py (sw_004)
  - fanout_execution_log: 001_switchboard_messaging.py (sw_001)

Guards:
  - connector_source_filters is dropped before source_filters to satisfy the FK
    (connector_source_filters.filter_id references source_filters.id ON DELETE CASCADE).
    The order here makes the drop non-reliant on CASCADE, but CASCADE on source_filters
    would handle it anyway. Both are guarded with IF EXISTS.
  - to_regclass checks are not needed for within-schema drops; IF EXISTS is sufficient.
  - All drops are idempotent and safe to run multiple times.

Downgrade recreates the original table schemas (columns + indexes) from
sw_001/sw_003/sw_004 for rollback fidelity. No data to restore.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_014"
down_revision = "sw_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # connector_source_filters has a FK to source_filters; drop it first.
    op.execute("DROP TABLE IF EXISTS connector_source_filters")
    op.execute("DROP TABLE IF EXISTS source_filters CASCADE")
    op.execute("DROP TABLE IF EXISTS email_metadata_refs")
    op.execute("DROP TABLE IF EXISTS fanout_execution_log")


def downgrade() -> None:
    # Recreate the original schemas (columns + indexes) from sw_001/sw_003/sw_004
    # for rollback fidelity. No data to restore.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fanout_execution_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_channel TEXT NOT NULL,
            source_id TEXT,
            tool_name TEXT NOT NULL,
            fanout_mode TEXT NOT NULL,
            join_policy TEXT NOT NULL,
            abort_policy TEXT NOT NULL,
            plan_payload JSONB NOT NULL,
            execution_payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fanout_execution_log_created_at
        ON fanout_execution_log (created_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS email_metadata_refs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            endpoint_identity TEXT NOT NULL,
            gmail_message_id TEXT NOT NULL,
            thread_id TEXT,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            received_at TIMESTAMPTZ NOT NULL,
            labels JSONB NOT NULL DEFAULT '[]'::jsonb,
            summary TEXT NOT NULL,
            tier INTEGER NOT NULL DEFAULT 2 CHECK (tier = 2),
            message_inbox_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_email_metadata_refs_endpoint_message
        ON email_metadata_refs (endpoint_identity, gmail_message_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_received_at_desc
        ON email_metadata_refs (received_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_sender_received_at
        ON email_metadata_refs (sender, received_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_metadata_refs_inbox_id
        ON email_metadata_refs (message_inbox_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_filters (
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
        CREATE TABLE IF NOT EXISTS connector_source_filters (
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
        CREATE INDEX IF NOT EXISTS ix_connector_source_filters_connector
        ON connector_source_filters (connector_type, endpoint_identity)
        WHERE enabled = true
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_connector_source_filters_filter_id
        ON connector_source_filters (filter_id)
        """
    )
