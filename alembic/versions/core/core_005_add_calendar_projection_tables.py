"""add unified calendar projection tables for workspace persistence

Revision ID: core_005
Revises: core_004
Create Date: 2026-02-22 00:00:00.000000

Adds app-native projection tables used by the calendar workspace:
- calendar_sources
- calendar_events
- calendar_event_instances
- calendar_sync_cursors
- calendar_action_log

The schema is optimized for:
- range-window queries (GiST indexes on event/instance time ranges)
- source lookups (source_id/source_key indexes and uniqueness)
- idempotent mutation/audit workflows (idempotency_key uniqueness)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_005"
down_revision = "core_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_sources (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_key TEXT NOT NULL UNIQUE,
            source_kind TEXT NOT NULL,
            lane TEXT NOT NULL DEFAULT 'user',
            provider TEXT,
            calendar_id TEXT,
            butler_name TEXT,
            display_name TEXT,
            writable BOOLEAN NOT NULL DEFAULT false,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT calendar_sources_lane_check
                CHECK (lane IN ('user', 'butler')),
            CONSTRAINT calendar_sources_source_key_nonempty
                CHECK (length(btrim(source_key)) > 0),
            CONSTRAINT calendar_sources_source_kind_nonempty
                CHECK (length(btrim(source_kind)) > 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_sources_lane_kind
        ON calendar_sources (lane, source_kind)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
            origin_ref TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT,
            timezone TEXT NOT NULL,
            starts_at TIMESTAMPTZ NOT NULL,
            ends_at TIMESTAMPTZ NOT NULL,
            all_day BOOLEAN NOT NULL DEFAULT false,
            status TEXT NOT NULL DEFAULT 'confirmed',
            visibility TEXT NOT NULL DEFAULT 'default',
            recurrence_rule TEXT,
            etag TEXT,
            origin_updated_at TIMESTAMPTZ,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT calendar_events_source_origin_unique
                UNIQUE (source_id, origin_ref),
            CONSTRAINT calendar_events_source_origin_nonempty
                CHECK (length(btrim(origin_ref)) > 0),
            CONSTRAINT calendar_events_window_check
                CHECK (ends_at > starts_at),
            CONSTRAINT calendar_events_status_check
                CHECK (status IN ('confirmed', 'tentative', 'cancelled'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_events_source_starts_at
        ON calendar_events (source_id, starts_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_events_starts_at
        ON calendar_events (starts_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_events_time_window_gist
        ON calendar_events USING GIST (tstzrange(starts_at, ends_at, '[)'))
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_event_instances (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_id UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
            source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
            origin_instance_ref TEXT NOT NULL,
            timezone TEXT NOT NULL,
            starts_at TIMESTAMPTZ NOT NULL,
            ends_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            is_exception BOOLEAN NOT NULL DEFAULT false,
            origin_updated_at TIMESTAMPTZ,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT calendar_event_instances_event_origin_unique
                UNIQUE (event_id, origin_instance_ref),
            CONSTRAINT calendar_event_instances_origin_ref_nonempty
                CHECK (length(btrim(origin_instance_ref)) > 0),
            CONSTRAINT calendar_event_instances_window_check
                CHECK (ends_at > starts_at),
            CONSTRAINT calendar_event_instances_status_check
                CHECK (status IN ('confirmed', 'tentative', 'cancelled'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_event_instances_source_starts_at
        ON calendar_event_instances (source_id, starts_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_event_instances_event_starts_at
        ON calendar_event_instances (event_id, starts_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_event_instances_time_window_gist
        ON calendar_event_instances USING GIST (tstzrange(starts_at, ends_at, '[)'))
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_sync_cursors (
            source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
            cursor_name TEXT NOT NULL DEFAULT 'default',
            sync_token TEXT,
            checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb,
            full_sync_required BOOLEAN NOT NULL DEFAULT false,
            last_synced_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_error_at TIMESTAMPTZ,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_id, cursor_name),
            CONSTRAINT calendar_sync_cursors_cursor_name_nonempty
                CHECK (length(btrim(cursor_name)) > 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_sync_cursors_last_synced_at
        ON calendar_sync_cursors (last_synced_at)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_action_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key TEXT NOT NULL UNIQUE,
            request_id TEXT,
            action_type TEXT NOT NULL,
            action_status TEXT NOT NULL DEFAULT 'pending',
            source_id UUID REFERENCES calendar_sources(id) ON DELETE SET NULL,
            event_id UUID REFERENCES calendar_events(id) ON DELETE SET NULL,
            instance_id UUID REFERENCES calendar_event_instances(id) ON DELETE SET NULL,
            origin_ref TEXT,
            action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            action_result JSONB,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            applied_at TIMESTAMPTZ,
            CONSTRAINT calendar_action_log_idempotency_key_nonempty
                CHECK (length(btrim(idempotency_key)) > 0),
            CONSTRAINT calendar_action_log_action_type_nonempty
                CHECK (length(btrim(action_type)) > 0),
            CONSTRAINT calendar_action_log_status_check
                CHECK (action_status IN ('pending', 'applied', 'failed', 'noop'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_action_log_request_id
        ON calendar_action_log (request_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_action_log_source_created_at
        ON calendar_action_log (source_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_action_log_event_created_at
        ON calendar_action_log (event_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_action_log_instance_created_at
        ON calendar_action_log (instance_id, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_calendar_action_log_instance_created_at")
    op.execute("DROP INDEX IF EXISTS ix_calendar_action_log_event_created_at")
    op.execute("DROP INDEX IF EXISTS ix_calendar_action_log_source_created_at")
    op.execute("DROP INDEX IF EXISTS ix_calendar_action_log_request_id")
    op.execute("DROP TABLE IF EXISTS calendar_action_log")

    op.execute("DROP INDEX IF EXISTS ix_calendar_sync_cursors_last_synced_at")
    op.execute("DROP TABLE IF EXISTS calendar_sync_cursors")

    op.execute("DROP INDEX IF EXISTS ix_calendar_event_instances_time_window_gist")
    op.execute("DROP INDEX IF EXISTS ix_calendar_event_instances_event_starts_at")
    op.execute("DROP INDEX IF EXISTS ix_calendar_event_instances_source_starts_at")
    op.execute("DROP TABLE IF EXISTS calendar_event_instances")

    op.execute("DROP INDEX IF EXISTS ix_calendar_events_time_window_gist")
    op.execute("DROP INDEX IF EXISTS ix_calendar_events_starts_at")
    op.execute("DROP INDEX IF EXISTS ix_calendar_events_source_starts_at")
    op.execute("DROP TABLE IF EXISTS calendar_events")

    op.execute("DROP INDEX IF EXISTS ix_calendar_sources_lane_kind")
    op.execute("DROP TABLE IF EXISTS calendar_sources")
