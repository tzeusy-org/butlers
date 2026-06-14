"""drop_episodes_entity_id

Revision ID: chronicler_016
Revises: chronicler_015
Create Date: 2026-06-14 00:00:00.000000

Drop the derived ``chronicler.episodes.entity_id`` column (added in
chronicler_013) and recreate ``v_episodes_corrected`` without it.

Background
----------
``episodes.entity_id`` was a single-valued *owner* column used for the
``chronicler_list_episodes(entity_id=...)`` / ``GET /api/chronicler/episodes
?entity_id=`` owner-only filter. It was superseded by the multi-entity
``episode_entities`` join table (chronicler_014) and the aggregated
``participant_entity_ids`` column it exposes on ``v_episodes_corrected``.

Per bu-cfsgy (owner decision 2026-06-13): single-owner deployment, no
external readers — the transition-window gate was removed and the derived
column is now fully dead in code (writers, readers, and the API filter were
removed in the same change). This migration removes the storage.

Schema change
-------------
1. Drop the partial index ``episodes_entity_id_idx`` (depends on the column).
2. Recreate ``v_episodes_corrected`` WITHOUT ``e.entity_id`` but retaining the
   aggregated ``participant_entity_ids`` column. ``CREATE OR REPLACE VIEW``
   cannot drop a column, so the view is dropped and recreated. The view body
   mirrors the chronicler_014 shape minus the ``e.entity_id`` projection /
   GROUP BY term.
3. Backfill any non-null ``episodes.entity_id`` owner link into
   ``episode_entities`` (role='owner', ``ON CONFLICT DO NOTHING``) so
   transition-window rows that only ever carried the derived column are not
   lost — chronicler_014 created the join table empty and never copied these.
4. Drop the column ``chronicler.episodes.entity_id`` (self-guarding via
   ``IF EXISTS``; the view no longer references it by this point).

``participant_entity_ids`` is preserved, so the API contract change is the
removal of the *owner-only* ``entity_id`` filter — callers use
``participant_entity_id`` instead.

Downgrade
---------
Re-adds the column (nullable, no data restored), recreates the index, and
restores the chronicler_014 view shape (with ``e.entity_id`` again). Data in
the dropped column is NOT recoverable; downgrade leaves ``entity_id = NULL``
for all rows.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "chronicler_016"
down_revision = "chronicler_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Drop the index that depends on the column ────────────────────────
    op.execute("DROP INDEX IF EXISTS episodes_entity_id_idx")

    # ── 2. Recreate v_episodes_corrected without e.entity_id ────────────────
    # CREATE OR REPLACE VIEW cannot remove a column, so drop + recreate.
    # ``episode_entities`` is created in chronicler_014 (same chain), so a
    # plain reference is safe here; guarded with to_regclass for defence
    # against out-of-order partial applications (cross-chain drop hazard).
    # Use an unqualified name so the check honours the active search_path
    # (the rest of this migration also references tables unqualified).
    bind = op.get_bind()
    if (
        bind.execute(text("SELECT to_regclass('episode_entities')")).scalar() is None
    ):  # pragma: no cover - defensive; never true in normal chain order
        raise RuntimeError(
            "chronicler_016: episode_entities is absent — "
            "chronicler_014 must be applied before this migration"
        )

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
            o.corrected_start_at,
            o.corrected_end_at,
            o.corrected_title,
            o.corrected_privacy,
            o.corrected_tombstone_at,
            o.corrected_at,
            o.note
    """)

    # ── 3. Preserve owner links before dropping the column ──────────────────
    # chronicler_014 created ``episode_entities`` empty (it did NOT backfill
    # from the existing ``episodes.entity_id`` values). Episodes set during the
    # chronicler_013 transition window that were never re-projected through the
    # post-014 adapter path therefore hold their owner link ONLY in the derived
    # column. Copy any such non-null link into the join table (role='owner')
    # before the drop so it survives — the drop is otherwise lossy and the
    # downgrade cannot restore the values. Idempotent via ON CONFLICT DO NOTHING.
    op.execute("""
        INSERT INTO episode_entities (episode_id, entity_id, role)
        SELECT id, entity_id, 'owner'
        FROM episodes
        WHERE entity_id IS NOT NULL
        ON CONFLICT (episode_id, entity_id) DO NOTHING
    """)

    # ── 4. Drop the derived column (self-guarding) ──────────────────────────
    op.execute("""
        ALTER TABLE episodes
        DROP COLUMN IF EXISTS entity_id
    """)


def downgrade() -> None:
    # ── Re-add the column (data NOT restored) ───────────────────────────────
    op.execute("""
        ALTER TABLE episodes
        ADD COLUMN IF NOT EXISTS entity_id UUID
    """)

    # ── Recreate the partial index ──────────────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS episodes_entity_id_idx
        ON episodes (entity_id, start_at DESC)
        WHERE tombstone_at IS NULL AND entity_id IS NOT NULL
    """)

    # ── Restore the chronicler_014 view shape (with e.entity_id) ────────────
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
