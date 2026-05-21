"""episode_entities

Revision ID: chronicler_014
Revises: chronicler_013
Create Date: 2026-05-21 00:00:00.000000

Add ``chronicler.episode_entities`` join table so each episode can be
associated with multiple entities (owner, organizer, participant).

Background
----------
Task §2 (bu-t0130, design PR #1867):
``chronicler.episodes`` currently carries a single nullable ``entity_id``
column (migration ``chronicler_013``, bu-f4755).  For Google Calendar
meetings with attendees, this means only the calendar-account owner is
tagged; other participants are invisible to the entity-activity aggregator.

This migration introduces a proper join table so one episode can link to
N entities with role semantics, and recreates ``v_episodes_corrected`` to
expose an aggregated ``participant_entity_ids UUID[]`` column.

Schema changes
--------------
- Creates ``chronicler.episode_entities`` with composite PK
  ``(episode_id, entity_id)`` and a CHECK-constrained ``role`` column.
  No FK on ``entity_id`` against ``public.entities`` (matches the
  existing chronicler convention — chronicler may boot before the
  relationship butler schema exists).
- Creates index ``episode_entities_entity_idx`` for efficient
  entity-first look-ups.
- Recreates ``v_episodes_corrected`` to append the aggregated
  ``participant_entity_ids UUID[]`` column.  The column NEVER returns
  NULL: episodes with no rows in ``episode_entities`` produce
  ``'{}'::uuid[]`` via ``COALESCE``.  Array order is role-precedence
  (owner=0, organizer=1, participant=2) then ``entity_id ASC``.

Downgrade reverts all three changes and restores ``v_episodes_corrected``
to the chronicler_013 shape (without ``participant_entity_ids``).
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_014"
down_revision = "chronicler_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Create episode_entities join table ──────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS episode_entities (
            episode_id  UUID NOT NULL REFERENCES episodes(id)
                          ON DELETE CASCADE,
            entity_id   UUID NOT NULL,
            role        TEXT NOT NULL DEFAULT 'participant'
                          CHECK (role IN ('owner', 'organizer', 'participant')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (episode_id, entity_id)
        )
    """)

    # ── Index for entity-first look-ups (efficient for activity queries) ────
    op.execute("""
        CREATE INDEX IF NOT EXISTS episode_entities_entity_idx
        ON episode_entities (entity_id, episode_id)
    """)

    # ── Recreate v_episodes_corrected to expose participant_entity_ids ───────
    # Appended at the end so existing column positions are preserved.
    # LEFT JOIN ensures episodes with no episode_entities rows get '{}'::uuid[].
    # GROUP BY all non-aggregate columns from the existing view shape.
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
            e.entity_id,
            COALESCE(
                array_agg(ee.entity_id ORDER BY
                    CASE ee.role
                        WHEN 'owner' THEN 0
                        WHEN 'organizer' THEN 1
                        ELSE 2
                    END,
                    ee.entity_id
                ) FILTER (WHERE ee.entity_id IS NOT NULL),
                '{}'::uuid[]
            ) AS participant_entity_ids
        FROM episodes e
        LEFT JOIN v_latest_overrides o
            ON o.target_kind = 'episode' AND o.target_id = e.id
        LEFT JOIN episode_entities ee
            ON ee.episode_id = e.id
        GROUP BY
            e.id,
            e.source_name,
            e.source_ref,
            e.episode_type,
            e.start_at,
            e.end_at,
            e.precision,
            e.title,
            e.payload,
            e.privacy,
            e.retention_days,
            e.tombstone_at,
            e.created_at,
            e.updated_at,
            e.entity_id,
            o.corrected_start_at,
            o.corrected_end_at,
            o.corrected_title,
            o.corrected_privacy,
            o.corrected_tombstone_at,
            o.corrected_at,
            o.note
    """)


def downgrade() -> None:
    # Restore v_episodes_corrected without participant_entity_ids (chronicler_013 shape).
    # PostgreSQL CREATE OR REPLACE VIEW cannot remove columns, so we must drop first.
    op.execute("DROP VIEW IF EXISTS v_episodes_corrected")
    op.execute("""
        CREATE VIEW v_episodes_corrected AS
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

    op.execute("DROP INDEX IF EXISTS episode_entities_entity_idx")

    op.execute("DROP TABLE IF EXISTS episode_entities")
