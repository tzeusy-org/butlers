"""create_chronicler_tables

Revision ID: chronicler_001
Revises:
Create Date: 2026-04-24 00:00:00.000000

Creates the Chronicler domain butler's storage primitives: point events,
overlapping episodes, episode-event links, user corrections (overrides),
projection checkpoints, source adapter state, and idempotency keys.

Per RFC 0014, every row carries source provenance, boundary precision,
privacy/retention metadata, and optional tombstone. Overlapping episodes
are permitted; corrections layer on top of canonical rows without
mutation.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "chronicler_001"
down_revision = None
branch_labels = ("chronicler",)
depends_on = None


def upgrade() -> None:
    # ── source_adapter_state ─────────────────────────────────────────────
    # One row per declared source; carries the compatibility contract.
    op.execute("""
        CREATE TABLE IF NOT EXISTS source_adapter_state (
            source_name TEXT PRIMARY KEY,
            chronicler_compatibility TEXT NOT NULL
                CHECK (chronicler_compatibility IN (
                    'supported', 'deferred', 'not_time_bearing', 'planned'
                )),
            read_surface TEXT,
            boundary_semantics TEXT,
            optional_schema BOOLEAN NOT NULL DEFAULT false,
            active BOOLEAN NOT NULL DEFAULT false,
            inactive_reason TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── projection_checkpoints ──────────────────────────────────────────
    # Per-adapter cursor state updated on every projection run.
    op.execute("""
        CREATE TABLE IF NOT EXISTS projection_checkpoints (
            source_name TEXT PRIMARY KEY REFERENCES source_adapter_state(source_name)
                ON DELETE CASCADE,
            watermark TIMESTAMPTZ,
            last_run_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_error TEXT,
            rows_projected BIGINT NOT NULL DEFAULT 0,
            run_count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── point_events ────────────────────────────────────────────────────
    # Instantaneous events with source provenance and precision.
    op.execute("""
        CREATE TABLE IF NOT EXISTS point_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
            source_ref TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            precision TEXT NOT NULL DEFAULT 'exact'
                CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
            title TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            privacy TEXT NOT NULL DEFAULT 'normal'
                CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
            retention_days INTEGER,
            tombstone_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_name, source_ref)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS point_events_occurred_at_idx
        ON point_events (occurred_at DESC)
        WHERE tombstone_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS point_events_source_name_idx
        ON point_events (source_name, occurred_at DESC)
        WHERE tombstone_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS point_events_event_type_idx
        ON point_events (event_type, occurred_at DESC)
        WHERE tombstone_at IS NULL
    """)

    # ── episodes ────────────────────────────────────────────────────────
    # Span-shaped events; end_at may be NULL for open episodes.
    op.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
            source_ref TEXT NOT NULL,
            episode_type TEXT NOT NULL,
            start_at TIMESTAMPTZ NOT NULL,
            end_at TIMESTAMPTZ,
            precision TEXT NOT NULL DEFAULT 'exact'
                CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
            title TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            privacy TEXT NOT NULL DEFAULT 'normal'
                CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
            retention_days INTEGER,
            tombstone_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_name, source_ref),
            CHECK (end_at IS NULL OR end_at >= start_at)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS episodes_start_at_idx
        ON episodes (start_at DESC)
        WHERE tombstone_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS episodes_end_at_idx
        ON episodes (end_at DESC)
        WHERE tombstone_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS episodes_open_idx
        ON episodes (start_at DESC)
        WHERE end_at IS NULL AND tombstone_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS episodes_source_name_idx
        ON episodes (source_name, start_at DESC)
        WHERE tombstone_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS episodes_episode_type_idx
        ON episodes (episode_type, start_at DESC)
        WHERE tombstone_at IS NULL
    """)

    # ── episode_event_links ─────────────────────────────────────────────
    # Many-to-many: episodes supported by point events.
    op.execute("""
        CREATE TABLE IF NOT EXISTS episode_event_links (
            episode_id UUID NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
            event_id UUID NOT NULL REFERENCES point_events(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'supports'
                CHECK (relation IN ('supports', 'boundary_start', 'boundary_end', 'evidence')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (episode_id, event_id, relation)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS episode_event_links_event_idx
        ON episode_event_links (event_id)
    """)

    # ── overrides ───────────────────────────────────────────────────────
    # User corrections layered on top of canonical rows.
    op.execute("""
        CREATE TABLE IF NOT EXISTS overrides (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_kind TEXT NOT NULL CHECK (target_kind IN ('episode', 'point_event')),
            target_id UUID NOT NULL,
            corrected_start_at TIMESTAMPTZ,
            corrected_end_at TIMESTAMPTZ,
            corrected_title TEXT,
            corrected_privacy TEXT
                CHECK (corrected_privacy IS NULL OR
                       corrected_privacy IN ('normal', 'sensitive', 'restricted')),
            corrected_tombstone_at TIMESTAMPTZ,
            note TEXT,
            submitted_by TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (
                corrected_start_at IS NOT NULL OR
                corrected_end_at IS NOT NULL OR
                corrected_title IS NOT NULL OR
                corrected_privacy IS NOT NULL OR
                corrected_tombstone_at IS NOT NULL OR
                note IS NOT NULL
            )
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS overrides_target_idx
        ON overrides (target_kind, target_id, created_at DESC)
    """)

    # ── idempotency_keys ────────────────────────────────────────────────
    # Optional registry for adapters to record projection attempts outside
    # the natural source-ref uniqueness (e.g. batch-level dedup).
    op.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name)
                ON DELETE CASCADE,
            key TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            hit_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (source_name, key)
        )
    """)

    # ── corrected views ─────────────────────────────────────────────────
    # v_episodes_corrected and v_point_events_corrected apply the latest
    # override per target. Canonical rows are never mutated; corrections
    # layer here.
    op.execute("""
        CREATE OR REPLACE VIEW v_latest_overrides AS
        SELECT DISTINCT ON (target_kind, target_id)
            target_kind,
            target_id,
            corrected_start_at,
            corrected_end_at,
            corrected_title,
            corrected_privacy,
            corrected_tombstone_at,
            note,
            created_at AS corrected_at
        FROM overrides
        ORDER BY target_kind, target_id, created_at DESC
    """)
    op.execute("""
        CREATE OR REPLACE VIEW v_episodes_corrected AS
        SELECT
            e.id,
            e.source_name,
            e.source_ref,
            e.episode_type,
            COALESCE(o.corrected_start_at, e.start_at) AS start_at,
            COALESCE(o.corrected_end_at, e.end_at) AS end_at,
            e.precision,
            COALESCE(o.corrected_title, e.title) AS title,
            e.payload,
            COALESCE(o.corrected_privacy, e.privacy) AS privacy,
            e.retention_days,
            COALESCE(o.corrected_tombstone_at, e.tombstone_at) AS tombstone_at,
            e.start_at AS canonical_start_at,
            e.end_at AS canonical_end_at,
            e.title AS canonical_title,
            e.privacy AS canonical_privacy,
            o.corrected_at,
            o.note AS correction_note,
            e.created_at,
            e.updated_at
        FROM episodes e
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'episode' AND o.target_id = e.id
    """)
    op.execute("""
        CREATE OR REPLACE VIEW v_point_events_corrected AS
        SELECT
            p.id,
            p.source_name,
            p.source_ref,
            p.event_type,
            COALESCE(o.corrected_start_at, p.occurred_at) AS occurred_at,
            p.precision,
            COALESCE(o.corrected_title, p.title) AS title,
            p.payload,
            COALESCE(o.corrected_privacy, p.privacy) AS privacy,
            p.retention_days,
            COALESCE(o.corrected_tombstone_at, p.tombstone_at) AS tombstone_at,
            p.occurred_at AS canonical_occurred_at,
            p.title AS canonical_title,
            p.privacy AS canonical_privacy,
            o.corrected_at,
            o.note AS correction_note,
            p.created_at,
            p.updated_at
        FROM point_events p
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'point_event' AND o.target_id = p.id
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_point_events_corrected")
    op.execute("DROP VIEW IF EXISTS v_episodes_corrected")
    op.execute("DROP VIEW IF EXISTS v_latest_overrides")
    op.execute("DROP TABLE IF EXISTS idempotency_keys")
    op.execute("DROP TABLE IF EXISTS overrides")
    op.execute("DROP TABLE IF EXISTS episode_event_links")
    op.execute("DROP TABLE IF EXISTS episodes")
    op.execute("DROP TABLE IF EXISTS point_events")
    op.execute("DROP TABLE IF EXISTS projection_checkpoints")
    op.execute("DROP TABLE IF EXISTS source_adapter_state")
