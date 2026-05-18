"""episodes_entity_id

Revision ID: chronicler_013
Revises: chronicler_012
Create Date: 2026-05-18 00:00:00.000000

Add ``entity_id`` column to ``chronicler.episodes`` to support filtering
episodes by the entity they belong to.

Background
----------
Task 12.5 (bu-aqe7n): The activity aggregator endpoint (task 9.12,
``GET /api/butlers/relationship/entities/{id}/activity``) cannot ship
without a way to filter chronicler episodes by entity. This migration adds
the ``entity_id`` column so that ``chronicler_list_episodes(entity_id=...)``
can filter at the SQL layer.

Schema change
-------------
- Adds ``entity_id UUID`` (nullable, no FK constraint) to ``chronicler.episodes``.
  No FK constraint is used because chronicler runs before the relationship
  butler schema exists in some environments, and the application layer already
  enforces entity existence via ``public.entities``.
- Recreates ``v_episodes_corrected`` to expose ``entity_id``.
- Adds a btree index ``episodes_entity_id_idx`` for efficient filtering.

The column is nullable: existing episodes without an associated entity keep
``entity_id = NULL``. Future adapters that produce entity-scoped episodes
(e.g. meeting participants via Google Calendar) will set ``entity_id``
at projection time.

Downgrade reverts all three changes.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_013"
down_revision = "chronicler_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Add entity_id column to episodes ────────────────────────────────────
    op.execute("""
        ALTER TABLE episodes
        ADD COLUMN IF NOT EXISTS entity_id UUID
    """)

    # ── Index for entity_id filtering (partial — only live rows) ────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS episodes_entity_id_idx
        ON episodes (entity_id, start_at DESC)
        WHERE tombstone_at IS NULL AND entity_id IS NOT NULL
    """)

    # ── Recreate v_episodes_corrected to expose entity_id ───────────────────
    # The view is replaced (not dropped/recreated) so concurrent readers see
    # an atomic swap. The new column is appended at the end.
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
            e.updated_at,
            e.entity_id
        FROM episodes e
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'episode' AND o.target_id = e.id
    """)


def downgrade() -> None:
    # Restore v_episodes_corrected without entity_id
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

    op.execute("DROP INDEX IF EXISTS episodes_entity_id_idx")

    op.execute("""
        ALTER TABLE episodes
        DROP COLUMN IF EXISTS entity_id
    """)
