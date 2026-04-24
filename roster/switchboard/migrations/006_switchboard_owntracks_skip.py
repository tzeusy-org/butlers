"""Seed a global ingestion rule that skips OwnTracks location events.

Revision ID: sw_006
Revises: sw_005
Create Date: 2026-04-25 00:00:00.000000

OwnTracks emits one webhook POST per location ping (potentially several per
minute during movement). Each event previously fell through to
``action='pass_through'`` and spawned a Switchboard LLM classification session
plus downstream butler LLM sessions — burning tokens on location data whose
value lives in the table itself, not in natural-language summaries.

This migration inserts a ``scope='global'`` rule with
``rule_type='source_channel'`` and ``action='skip'``. When the IngestionPolicy
evaluator sees an event with ``source_channel='owntracks'``, it short-circuits
the pipeline — the row still lands in ``public.ingestion_events`` for direct
DB querying by location-aware butlers, but no LLM session is created.

The rule is disabled via ``UPDATE switchboard.ingestion_rules SET enabled=false
WHERE id='00000000-0000-0000-0001-000000000070'`` if LLM routing is ever wanted
back for OwnTracks.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_006"
down_revision = "sw_005"
branch_labels = None
depends_on = None


_RULE_ID = "00000000-0000-0000-0001-000000000070"


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
            'source_channel',
            '{{"source_channel": "owntracks"}}',
            'skip',
            10,
            TRUE,
            'Skip OwnTracks location pings',
            'OwnTracks webhook events bypass LLM classification. Rows still land in public.ingestion_events for direct DB querying.',
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM ingestion_rules WHERE id = '{_RULE_ID}'")
