"""core_136 — calendar_event_proposals: per-butler proposal staging table.

Revision ID: core_136
Revises: core_135
Create Date: 2026-06-21 00:00:00.000000

Creates ``calendar_event_proposals`` in each butler schema to back the
calendar proposals lane (epic bu-fh8drm, design D2).

The table stores butler-generated event proposals before they are accepted
onto the user's calendar.  Producers write rows idempotently via
``source_event_id``; the pending projection is served by the status index.

Columns
-------
id               UUID PK
butler_name      TEXT NOT NULL  — which butler produced the proposal
title            TEXT NOT NULL  — event title
start_at         TIMESTAMPTZ NOT NULL
end_at           TIMESTAMPTZ NOT NULL
description      TEXT           — optional long description
location         TEXT
timezone         TEXT NOT NULL DEFAULT 'UTC'
source_event_id  TEXT UNIQUE    — idempotency key (producer dedup)
source_snippet   TEXT           — excerpt from the source that triggered the proposal
confidence       REAL           — 0.0–1.0 producer confidence
entity_ids       UUID[]         — associated entity IDs (from public.entities)
status           TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'accepted', 'dismissed'))
accepted_event_id UUID          — FK to calendar_events.id, set on accept
created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()

Indexes
-------
uq_calendar_event_proposals_source_event_id   UNIQUE on source_event_id (partial: NOT NULL)
ix_calendar_event_proposals_status            on status (pending projection)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_136"
down_revision = "core_135"
branch_labels = None
depends_on = None

_TABLE = "calendar_event_proposals"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name      TEXT NOT NULL,
            title            TEXT NOT NULL,
            start_at         TIMESTAMPTZ NOT NULL,
            end_at           TIMESTAMPTZ NOT NULL,
            description      TEXT,
            location         TEXT,
            timezone         TEXT NOT NULL DEFAULT 'UTC',
            source_event_id  TEXT,
            source_snippet   TEXT,
            confidence       REAL,
            entity_ids       UUID[] NOT NULL DEFAULT '{{}}',
            status           TEXT NOT NULL DEFAULT 'pending',
            accepted_event_id UUID REFERENCES calendar_events(id) ON DELETE SET NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_calendar_event_proposals_status
                CHECK (status IN ('pending', 'accepted', 'dismissed')),
            CONSTRAINT chk_calendar_event_proposals_window
                CHECK (end_at > start_at),
            CONSTRAINT chk_calendar_event_proposals_confidence
                CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
        )
    """)

    # Partial unique on source_event_id so NULL rows are never deduplicated
    # against each other but non-NULL values enforce producer idempotency.
    op.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_event_proposals_source_event_id
        ON {_TABLE} (source_event_id)
        WHERE source_event_id IS NOT NULL
    """)

    # Index supporting the pending projection (list of unactioned proposals).
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_calendar_event_proposals_status
        ON {_TABLE} (status)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_calendar_event_proposals_status")
    op.execute("DROP INDEX IF EXISTS uq_calendar_event_proposals_source_event_id")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
