"""education_tables

Revision ID: education_001
Revises:
Create Date: 2026-02-26 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "education_001"
down_revision = None
branch_labels = ("education",)
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS education")

    # --- education.mind_maps ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS education.mind_maps (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title        TEXT NOT NULL,
            root_node_id UUID,
            status       TEXT NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active', 'completed', 'abandoned')),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- education.mind_map_nodes ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS education.mind_map_nodes (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            mind_map_id      UUID NOT NULL REFERENCES education.mind_maps(id) ON DELETE CASCADE,
            label            TEXT NOT NULL,
            description      TEXT,
            depth            INTEGER NOT NULL DEFAULT 0,
            mastery_score    FLOAT NOT NULL DEFAULT 0.0,
            mastery_status   TEXT NOT NULL DEFAULT 'unseen'
                                 CHECK (mastery_status IN (
                                     'unseen', 'diagnosed', 'learning', 'reviewing', 'mastered'
                                 )),
            ease_factor      FLOAT NOT NULL DEFAULT 2.5,
            repetitions      INTEGER NOT NULL DEFAULT 0,
            next_review_at   TIMESTAMPTZ,
            last_reviewed_at TIMESTAMPTZ,
            effort_minutes   INTEGER,
            metadata         JSONB NOT NULL DEFAULT '{}',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Add FK from mind_maps.root_node_id -> mind_map_nodes.id (after nodes table exists).
    # ON DELETE SET NULL: deleting a root node clears root_node_id rather than blocking
    # the delete or cascade-deleting the entire mind map.
    op.execute("""
        ALTER TABLE education.mind_maps
            ADD CONSTRAINT fk_root
            FOREIGN KEY (root_node_id)
            REFERENCES education.mind_map_nodes(id)
            ON DELETE SET NULL
    """)

    # --- education.mind_map_edges ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS education.mind_map_edges (
            parent_node_id UUID NOT NULL
                REFERENCES education.mind_map_nodes(id) ON DELETE CASCADE,
            child_node_id  UUID NOT NULL
                REFERENCES education.mind_map_nodes(id) ON DELETE CASCADE,
            edge_type      TEXT NOT NULL DEFAULT 'prerequisite'
                               CHECK (edge_type IN ('prerequisite', 'related')),
            PRIMARY KEY (parent_node_id, child_node_id)
        )
    """)

    # --- education.quiz_responses ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS education.quiz_responses (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            node_id       UUID NOT NULL REFERENCES education.mind_map_nodes(id) ON DELETE CASCADE,
            mind_map_id   UUID NOT NULL REFERENCES education.mind_maps(id) ON DELETE CASCADE,
            question_text TEXT NOT NULL,
            user_answer   TEXT,
            quality       INTEGER NOT NULL CHECK (quality BETWEEN 0 AND 5),
            response_type TEXT NOT NULL DEFAULT 'review'
                              CHECK (response_type IN ('diagnostic', 'teach', 'review')),
            session_id    UUID,
            responded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- education.analytics_snapshots ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS education.analytics_snapshots (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            mind_map_id   UUID REFERENCES education.mind_maps(id) ON DELETE CASCADE,
            snapshot_date DATE NOT NULL,
            metrics       JSONB NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- indexes ---
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mmn_map_status
            ON education.mind_map_nodes (mind_map_id, mastery_status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mmn_next_review
            ON education.mind_map_nodes (next_review_at)
            WHERE next_review_at IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mme_child
            ON education.mind_map_edges (child_node_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qr_node
            ON education.quiz_responses (node_id, responded_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qr_map_date
            ON education.quiz_responses (mind_map_id, responded_at DESC)
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_map_date
            ON education.analytics_snapshots (mind_map_id, snapshot_date)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS education.analytics_snapshots")
    op.execute("DROP TABLE IF EXISTS education.quiz_responses")
    op.execute("DROP TABLE IF EXISTS education.mind_map_edges")
    op.execute("ALTER TABLE education.mind_maps DROP CONSTRAINT IF EXISTS fk_root")
    op.execute("DROP TABLE IF EXISTS education.mind_map_nodes")
    op.execute("DROP TABLE IF EXISTS education.mind_maps")
    op.execute("DROP SCHEMA IF EXISTS education CASCADE")
