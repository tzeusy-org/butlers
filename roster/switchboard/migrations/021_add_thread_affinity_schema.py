"""Add thread affinity schema to routing_log and create thread_affinity_settings table.

Revision ID: sw_021
Revises: sw_020
Create Date: 2026-02-23 00:00:00.000000

Migration notes:
- Adds routing_log.thread_id (TEXT, nullable) for email thread identity.
- Adds routing_log.source_channel (TEXT, nullable) for channel annotation.
- Creates partial index idx_routing_log_thread_affinity for the lookup hot path:
    (thread_id, created_at DESC) WHERE thread_id IS NOT NULL AND source_channel = 'email'.
- Creates thread_affinity_settings table for:
    * Global enable/disable toggle (thread_affinity_enabled)
    * TTL days (thread_affinity_ttl_days, default 30)
    * Per-thread overrides (JSONB keyed by thread_id: "disabled" | "force:<butler>")
- Populates thread_affinity_settings with a single default seed row.
- Backfill of routing_log.source_channel from historical data is NOT performed;
  affinity starts from newly logged routed email threads (per spec §8 rollout notes).

See docs/switchboard/thread_affinity_routing.md for full spec.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_021"
down_revision = "sw_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- routing_log schema extension ---
    # Add thread_id for email thread identity (NULL for non-email channels).
    op.execute(
        """
        ALTER TABLE routing_log
        ADD COLUMN IF NOT EXISTS thread_id TEXT,
        ADD COLUMN IF NOT EXISTS source_channel TEXT
        """
    )

    # Partial index for thread-affinity lookup hot path.
    # Only indexes rows where thread_id is set and source_channel is 'email'.
    # Ordered by created_at DESC to efficiently find the most recent routing decisions.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_routing_log_thread_affinity
        ON routing_log (thread_id, created_at DESC)
        WHERE thread_id IS NOT NULL AND source_channel = 'email'
        """
    )

    # --- thread_affinity_settings table ---
    # Stores global enable/disable, TTL configuration, and per-thread overrides.
    # Designed as a single-row settings table (id=1) plus an overrides JSONB column
    # to avoid per-thread row proliferation.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_affinity_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            thread_affinity_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            thread_affinity_ttl_days INTEGER NOT NULL DEFAULT 30,
            thread_overrides JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT thread_affinity_settings_singleton CHECK (id = 1),
            CONSTRAINT thread_affinity_ttl_days_positive CHECK (thread_affinity_ttl_days > 0)
        )
        """
    )

    op.execute(
        """
        COMMENT ON TABLE thread_affinity_settings IS
        'Singleton settings row (id=1) for email thread-affinity routing. '
        'Controls global enable/disable, TTL window, and per-thread overrides. '
        'See docs/switchboard/thread_affinity_routing.md sections 5 and 6.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN thread_affinity_settings.thread_affinity_enabled IS
        'Global enable/disable for thread-affinity routing. When FALSE, affinity '
        'is skipped for all threads and the pipeline falls through to LLM classification.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN thread_affinity_settings.thread_affinity_ttl_days IS
        'Max age in days for routing history rows considered for affinity lookup. '
        'Default 30 days. Rows older than this window are treated as stale.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN thread_affinity_settings.thread_overrides IS
        'Per-thread override map: {"<thread_id>": "disabled" | "force:<butler>"}. '
        '"disabled" suppresses affinity for that thread. '
        '"force:<butler>" routes directly to the named butler regardless of history.'
        """
    )

    # Seed the singleton settings row with defaults.
    op.execute(
        """
        INSERT INTO thread_affinity_settings (id, thread_affinity_enabled, thread_affinity_ttl_days, thread_overrides)
        VALUES (1, TRUE, 30, '{}')
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS thread_affinity_settings")
    op.execute("DROP INDEX IF EXISTS idx_routing_log_thread_affinity")
    op.execute("ALTER TABLE routing_log DROP COLUMN IF EXISTS source_channel")
    op.execute("ALTER TABLE routing_log DROP COLUMN IF EXISTS thread_id")
