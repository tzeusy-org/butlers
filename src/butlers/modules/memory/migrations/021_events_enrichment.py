"""events_enrichment — mem_021

Enrich the memory_events table with structured provenance columns and create
the embedding_versions catalogue.

Part (a) — memory_events enrichment columns
-------------------------------------------
The existing memory_events table stores only (event_type, actor, tenant_id,
payload, created_at).  Three additional columns improve observability and
enable structured queries without JSON unpacking:

  request_id   TEXT           — request trace ID for correlation across the
                                 system (same value used in episodes / facts /
                                 rules created during the same request).
  memory_type  TEXT           — discriminator: 'episode' | 'fact' | 'rule' |
                                 NULL (NULL = non-memory-item events such as
                                 entity_merge, episode_consolidated).
  memory_id    UUID           — FK-like pointer to the affected memory row;
                                 left NULL for events that span many rows or
                                 have no single canonical item (e.g. bulk
                                 consolidation events).
  actor_butler TEXT           — the butler that owns / generated the event,
                                 separate from the generic ``actor`` field so
                                 callers can filter by owning butler without
                                 parsing payload.

All four columns are nullable for backward compatibility: existing rows and
callers that don't set them will simply have NULL.

A partial index on (actor_butler, event_type, created_at) supports the common
pattern of fetching recent events for a specific butler.

Part (b) — embedding_versions table
------------------------------------
Tracks the embedding model(s) used to generate vectors stored in facts, rules,
and the memory catalog.  This allows safe schema migrations (e.g. reindexing)
when a model is replaced.

Seed row: all-MiniLM-L6-v2, dimension 384, currently active.

Revision ID: mem_021
Revises: mem_020
Create Date: 2026-03-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_021"
down_revision = "mem_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Part (a): Add enrichment columns to memory_events
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE memory_events
            ADD COLUMN IF NOT EXISTS request_id   TEXT,
            ADD COLUMN IF NOT EXISTS memory_type  TEXT,
            ADD COLUMN IF NOT EXISTS memory_id    UUID,
            ADD COLUMN IF NOT EXISTS actor_butler TEXT
    """)

    # Partial index: recent events per butler/type (skips NULL actor_butler rows
    # which are system-level events like entity_merge).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_events_actor_butler_type
        ON memory_events (actor_butler, event_type, created_at DESC)
        WHERE actor_butler IS NOT NULL
    """)

    # -------------------------------------------------------------------------
    # Part (b): Create embedding_versions catalogue table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS embedding_versions (
            id          SERIAL PRIMARY KEY,
            model_name  TEXT    NOT NULL,
            dimension   INTEGER NOT NULL,
            description TEXT,
            active      BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT uq_embedding_versions_model UNIQUE (model_name)
        )
    """)

    # Seed row for the current model used throughout the memory module.
    op.execute("""
        INSERT INTO embedding_versions (model_name, dimension, description, active)
        VALUES (
            'all-MiniLM-L6-v2',
            384,
            'Sentence-Transformers all-MiniLM-L6-v2; 384-dimensional cosine embeddings',
            TRUE
        )
        ON CONFLICT (model_name) DO NOTHING
    """)


def downgrade() -> None:
    # Drop embedding_versions
    op.execute("DROP TABLE IF EXISTS embedding_versions CASCADE")

    # Drop the partial index on memory_events
    op.execute("DROP INDEX IF EXISTS idx_memory_events_actor_butler_type")

    # Drop the enrichment columns from memory_events
    op.execute("""
        ALTER TABLE memory_events
            DROP COLUMN IF EXISTS actor_butler,
            DROP COLUMN IF EXISTS memory_id,
            DROP COLUMN IF EXISTS memory_type,
            DROP COLUMN IF EXISTS request_id
    """)
