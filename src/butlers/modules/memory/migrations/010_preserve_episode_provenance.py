"""Preserve durable episode provenance without retaining episode content.

Revision ID: mem_010
Revises: mem_009
"""

from __future__ import annotations

from alembic import op

revision = "mem_010"
down_revision = "mem_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install content-free tombstones before allowing source episode deletion."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS episode_tombstones (
            episode_id UUID PRIMARY KEY,
            deleted_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION record_episode_tombstone()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            INSERT INTO episode_tombstones (episode_id)
            VALUES (OLD.id)
            ON CONFLICT (episode_id) DO NOTHING;
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_episodes_preserve_provenance ON episodes")
    op.execute(
        """
        CREATE TRIGGER trg_episodes_preserve_provenance
        BEFORE DELETE ON episodes
        FOR EACH ROW EXECUTE FUNCTION record_episode_tombstone()
        """
    )

    # Source provenance is durable evidence, not a live foreign-key
    # relationship.  The deletion trigger above establishes the bounded,
    # content-free referent before these source ids can outlive an episode.
    op.execute("ALTER TABLE facts DROP CONSTRAINT IF EXISTS facts_source_episode_id_fkey")
    op.execute("ALTER TABLE rules DROP CONSTRAINT IF EXISTS rules_source_episode_id_fkey")


def downgrade() -> None:
    # Reintroducing the old foreign keys would fail for intentionally retained
    # expired source ids and would force destructive provenance erasure.
    raise NotImplementedError("Episode provenance migration is intentionally non-reversible")
