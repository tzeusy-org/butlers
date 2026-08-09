"""Seed a narrow global metadata-only rule for Spotify spoken sessions.

Revision ID: sw_030
Revises: sw_029
Create Date: 2026-08-09 00:00:00.000000

Spotify spoken-session envelopes are capture-only evidence. Their
``ingestion_tier='metadata'`` annotation controls persistence shape, but the
Switchboard bypasses classification/routing only from a pre-resolved global
``metadata_only`` decision. The stable ``spotify:spoken:`` event-id prefix
limits this rule to spoken sessions; music context, digest, and summary events
remain classifiable and routable.
"""

from __future__ import annotations

from alembic import op

revision = "sw_030"
down_revision = "sw_029"
branch_labels = None
depends_on = None

_RULE_ID = "00000000-0000-0000-0001-000000000111"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO ingestion_rules
          (id, scope, rule_type, condition, action, priority, enabled,
           name, description, created_by)
        VALUES
          (
            '{_RULE_ID}',
            'global',
            'substring',
            '{{"pattern": "spotify:spoken:"}}',
            'metadata_only',
            10,
            TRUE,
            'Metadata-only Spotify spoken playback sessions',
            'Spotify spoken-session evidence (external_event_id prefix spotify:spoken:) bypasses LLM classification and butler routing while remaining durably persisted. Music context, digest, and summary events remain fully routable.',
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM ingestion_rules WHERE id = '{_RULE_ID}'")
