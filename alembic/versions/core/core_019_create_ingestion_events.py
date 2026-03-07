"""create_ingestion_events: add shared.ingestion_events table

Revision ID: core_019
Revises: core_018
Create Date: 2026-03-07 00:00:00.000000

Creates the shared.ingestion_events table — the canonical first-class record
of every event that enters the butler ecosystem via a connector.  One row
exists per accepted ingest envelope after deduplication.  The UUID7 primary
key is the request_id returned to connectors and propagated to all downstream
sessions.

This migration runs once per butler schema context but is fully idempotent
via CREATE TABLE IF NOT EXISTS.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_019"
down_revision = "core_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.ingestion_events (
            id                       UUID PRIMARY KEY,
            received_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_channel           TEXT NOT NULL,
            source_provider          TEXT NOT NULL,
            source_endpoint_identity TEXT NOT NULL,
            source_sender_identity   TEXT,
            source_thread_identity   TEXT,
            external_event_id        TEXT NOT NULL,
            dedupe_key               TEXT NOT NULL,
            dedupe_strategy          TEXT NOT NULL,
            ingestion_tier           TEXT NOT NULL,
            policy_tier              TEXT NOT NULL,
            triage_decision          TEXT,
            triage_target            TEXT,
            CONSTRAINT uq_ingestion_events_dedupe_key UNIQUE (dedupe_key)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ingestion_events_received_at
        ON shared.ingestion_events (received_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ingestion_events_source_channel
        ON shared.ingestion_events (source_channel, received_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ingestion_events_source_channel")
    op.execute("DROP INDEX IF EXISTS ix_ingestion_events_received_at")
    op.execute("DROP TABLE IF EXISTS shared.ingestion_events")
